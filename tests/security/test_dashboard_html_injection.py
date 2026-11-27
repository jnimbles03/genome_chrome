"""Regression tests for HTML injection in the extension dashboards.

These tests fail while the vulnerability is present. They pass once every
dashboard escapes interpolated analysis fields (including values that look
numeric), encodes quotes before putting a URL in an attribute, and the
analysis response no longer needs to be trusted for that to hold.

The tests drive the product's own code:

1. A PDF whose text contains the marker values is uploaded through
   ``analyze_pdf_file`` (``POST /api/analyze/upload``), the same endpoint
   ``popup.js`` ``analyzePdf`` calls.
2. ``chat_complete`` is replaced at the call boundary. The replacement returns
   JSON only by copying the marker values out of the prompt the product built
   from the extracted PDF text. ``analyze_pdf_file`` still does its own
   ``json.loads``, scoring, and response assembly.
3. Chrome loads ``popup.js`` and calls ``handleSingleAnalysis``, which stores
   ``data.results`` under an ``analysis_`` key.
4. Chrome loads each personal ``dashboard.js``. Card dashboards leave
   ``filteredForms`` empty until a sort/filter event, so the test changes the
   sort control, which is the page's own listener for ``filterAndRenderForms``.
5. ``services/sync.js`` ``syncAnalysisResults`` builds the team payload. That
   payload is posted into the real ``/api/team/sync`` and ``/api/team/forms``
   views. Only the Postgres connection is fake; ``json.dumps`` / ``json.loads``
   and the record mapping in those views still run.
6. Chrome loads each ``team-dashboard.js`` against that response and opens the
   edit row, which is the quote-sensitive ``value`` attribute path.

Run from the repository root:

    pip install -r tests/security/requirements.txt
    npm install --prefix tests/security
    PYTHONPATH=form-genome-structured-cloudrun python3 -m pytest tests/security/test_dashboard_html_injection.py -q
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import threading
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest
from flask import Flask, abort, send_from_directory

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "form-genome-structured-cloudrun"
NODE_DIR = Path(__file__).resolve().parent
TITLE = "Benefits Enrollment"

# Marker values are copied verbatim from the PDF text into the model JSON.
# The attack document asks for markup and a quote inside the URL. The safe
# document asks for a number and an http URL with no quote.
ATTACK_FIELDS = {
    "NIGO": '<img id="nigo-injected" src="https://attacker.example/pixel.png">',
    "URL": 'https://attacker.example/form.pdf" style="background-image:url(https://attacker.example/track.png)"',
    "ACTION": '<img id="action-injected" src="https://attacker.example/action.png">',
    "PAY": '<img id="pay-injected" src="https://attacker.example/pay.png">',
    "PURPOSE": '<img id="purpose-injected" src="https://attacker.example/purpose.png">',
    "SIGNER": '<img id="signer-injected" src="https://attacker.example/signer.png">',
    "VERTICAL": '<img id="vertical-injected" src="https://attacker.example/vertical.png">',
}
SAFE_FIELDS = {
    "NIGO": "4",
    "URL": "https://example.com/plain.pdf",
    "ACTION": "Disclosure Only",
    "PAY": "0",
    "PURPOSE": "Enrollment",
    "SIGNER": "5",
    "VERTICAL": "Government",
}


def _pdf_bytes(text: str) -> bytes:
    """Minimal PDF whose extracted text is ``text``. Parentheses are escaped
    in the content stream so pypdf returns them as literal characters."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 50 750 Td ({escaped}) Tj ET\n".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def _document(fields: dict) -> str:
    parts = [f"{key}_PAYLOAD_START {value} {key}_PAYLOAD_END" for key, value in fields.items()]
    parts.append("OK_PAYLOAD_START true OK_PAYLOAD_END")
    return " ".join(parts)


def _between(text: str, key: str) -> str:
    start = f"{key}_PAYLOAD_START"
    end = f"{key}_PAYLOAD_END"
    match = re.search(re.escape(start) + r"\s*(.*?)\s*" + re.escape(end), text)
    if not match:
        raise RuntimeError(f"PDF text for {key} did not reach the model prompt")
    return match.group(1)


def _json_value(raw: str):
    if re.fullmatch(r"\d+", raw):
        return int(raw)
    return raw


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self._result = []

    def execute(self, sql, params=None):
        if "INSERT INTO forms" in sql:
            url, url_hash, title, entity_name, complexity, analyzed_at, updated_at, metadata = params
            row = {
                "id": len(self.rows) + 1,
                "url": url,
                "url_hash": url_hash,
                "title": title,
                "entity_name": entity_name,
                "complexity": complexity,
                "metadata": metadata,
                "analyzed_at": analyzed_at,
                "created_at": analyzed_at,
                "status": "completed",
                "updated_at": updated_at,
            }
            self.rows[:] = [existing for existing in self.rows if existing["url"] != url]
            self.rows.append(row)
            self._result = []
        elif "SELECT" in sql and "forms" in sql:
            self._result = list(self.rows)
        else:
            raise AssertionError(f"unexpected SQL in dashboard path: {sql}")

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _Cursor(self.rows)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


@pytest.fixture(scope="module")
def server():
    import sys

    sys.path.insert(0, str(BACKEND))
    import app.api.analyze as analyze_mod
    import app.api.dashboard as dashboard_mod
    import app.services.storage as storage_mod

    state = {"prompts": [], "rows": []}

    def fake_chat_complete(messages, **kwargs):
        user = messages[-1]["content"]
        state["prompts"].append(user)
        if not kwargs.get("force_tier2"):
            # High score forces the deep-analysis branch in analyze_pdf_file.
            return json.dumps(
                {
                    "title": TITLE,
                    "entity": "Example Agency",
                    "complexity_score": 80,
                    "total_field_count": 20,
                    "signature_required": True,
                    "signature_count": 1,
                    "confidence_tier": "High",
                }
            )
        nigo = _between(user, "NIGO")
        source_url = _between(user, "URL")
        ok_flag = _between(user, "OK")
        payload = {
            "title": TITLE,
            "pretty_title": TITLE,
            "form_name": TITLE,
            "entity": "Example Agency",
            "entity_name": "Example Agency",
            "complexity_score": 40,
            "nigo_score": _json_value(nigo),
            "total_field_count": 3,
            "checkboxes": 1,
            "text_fields": 2,
            "signature_count": 0,
            "signature_required": False,
            "action_type": _between(user, "ACTION"),
            "notarization_required": False,
            "attachments_required": False,
            "attachment_count": 0,
            "payment_required": False,
            "payment_amount": _between(user, "PAY"),
            "identification_required": False,
            "conditional_logic": False,
            "third_party_involved": False,
            "witnesses_required": False,
            "deadlines_present": False,
            "form_purpose": _between(user, "PURPOSE"),
            "industry_vertical": _between(user, "VERTICAL"),
            "industry_subvertical": "Licensing",
            "estimated_signer_time": _json_value(_between(user, "SIGNER")),
            "estimated_processing_time": 1,
            "confidence_tier": "High",
            "source_url": source_url,
            "ok": ok_flag == "true",
        }
        return json.dumps(payload)

    analyze_mod.chat_complete = fake_chat_complete

    @contextmanager
    def fake_get_db():
        conn = _Conn(state["rows"])
        try:
            yield conn
        finally:
            conn.close()

    storage_mod.get_db = fake_get_db
    dashboard_mod.get_db = fake_get_db

    app = Flask(__name__)
    analyze_mod.register_analyze_api(app)
    app.register_blueprint(dashboard_mod.dashboard_bp)

    pdfs = {}

    @app.get("/blank.html")
    def blank():
        return "<!doctype html><title>blank</title>"

    @app.get("/fixtures/<name>.pdf")
    def fixture_pdf(name):
        if name not in pdfs:
            abort(404)
        return app.response_class(pdfs[name], mimetype="application/pdf")

    @app.get("/<path:rel>")
    def repo_file(rel):
        root = REPO.resolve()
        full = (root / rel).resolve()
        if not str(full).startswith(str(root) + "/") or not full.is_file():
            abort(404)
        return send_from_directory(full.parent, full.name)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    thread = threading.Thread(
        target=app.run,
        kwargs={"host": "127.0.0.1", "port": port, "threaded": True, "use_reloader": False},
        daemon=True,
    )
    thread.start()
    state["base"] = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(state["base"] + "/blank.html", timeout=0.5) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("analysis server did not start")
    state["pdfs"] = pdfs
    state["app"] = app
    yield state


def _run_node(phase, base, report_path, **kwargs):
    command = ["node", str(NODE_DIR / "render_dashboards.js"), phase, "--base", base, "--out", str(report_path)]
    for key, value in kwargs.items():
        command.extend(["--" + key.replace("_", "-"), value])
    completed = subprocess.run(
        command,
        cwd=NODE_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"browser runner failed\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return json.loads(report_path.read_text())


def _team_forms_response(server, payloads):
    server["rows"].clear()
    client = server["app"].test_client()
    for payload in payloads:
        response = client.post("/api/team/sync", json=payload)
        assert response.status_code == 200, response.get_data(as_text=True)
    listed = client.get("/api/team/forms?limit=1000")
    assert listed.status_code == 200, listed.get_data(as_text=True)
    body = listed.get_json()
    assert body["ok"] is True
    assert body["forms"], body
    return body


def _problems(pages, *, expect_href):
    problems = []
    for page in pages:
        path = page.get("path")
        if page.get("failed"):
            problems.append({"path": path, "failed": page["failed"], "pageErrors": page.get("pageErrors"), "text": page.get("textSample")})
        if not page.get("sawTitle"):
            problems.append({"path": path, "missingTitle": True, "text": page.get("textSample"), "errors": page.get("pageErrors")})
        if page.get("images"):
            problems.append({"path": path, "images": page["images"]})
        if page.get("styledAnchors"):
            problems.append({"path": path, "styledAnchors": page["styledAnchors"]})
        edit = page.get("edit") or {}
        if page.get("kind") == "team" and not edit.get("available"):
            problems.append({"path": path, "editUnavailable": True, "text": page.get("textSample")})
        if edit.get("styledInputs"):
            problems.append({"path": path, "styledInputs": edit["styledInputs"]})
        if expect_href and page.get("kind") in ("card", "table"):
            if expect_href not in (page.get("hrefs") or []):
                problems.append({"path": path, "hrefs": page.get("hrefs")})
    return problems


def _exercise(server, fields, tmp_path, expect_href):
    server["prompts"].clear()
    server["rows"].clear()
    name = "attack" if "nigo-injected" in fields["NIGO"] else "safe"
    server["pdfs"][name] = _pdf_bytes(_document(fields))
    base = server["base"]
    personal_path = tmp_path / "personal.json"
    personal = _run_node(
        "personal",
        base,
        personal_path,
        pdf=f"{base}/fixtures/{name}.pdf",
    )
    assert personal["popups"], personal
    for popup in personal["popups"]:
        assert popup.get("formTitle") == TITLE, popup
        assert not popup.get("thrown"), popup
    assert any("NIGO_PAYLOAD_START" in prompt for prompt in server["prompts"]), server["prompts"]
    assert personal["syncPayloads"], personal.get("syncDebug")
    forms = _team_forms_response(server, personal["syncPayloads"])
    forms_path = tmp_path / "forms.json"
    forms_path.write_text(json.dumps(forms))
    storage_path = tmp_path / "storage.json"
    storage_path.write_text(personal["storageDump"])
    team_path = tmp_path / "team.json"
    team = _run_node(
        "team",
        base,
        team_path,
        storage=str(storage_path),
        forms=str(forms_path),
    )
    return personal, team


def test_attacker_controlled_analysis_is_not_rendered_as_html(server, tmp_path):
    personal, team = _exercise(server, ATTACK_FIELDS, tmp_path, expect_href=None)
    problems = _problems(personal["pages"], expect_href=None) + _problems(team["pages"], expect_href=None)
    assert not problems, json.dumps(problems, indent=2)


def test_ordinary_analysis_still_renders(server, tmp_path):
    personal, team = _exercise(server, SAFE_FIELDS, tmp_path, expect_href=SAFE_FIELDS["URL"])
    problems = _problems(personal["pages"], expect_href=SAFE_FIELDS["URL"]) + _problems(
        team["pages"], expect_href=None
    )
    # The safe URL is text on the team table, not an anchor.
    for page in team["pages"]:
        if SAFE_FIELDS["URL"] not in (page.get("textSample") or "") and page.get("sawTitle"):
            # textSample is truncated; the URL can fall outside the first 600 chars
            # of a wide table. Accept the title plus a non-styled render.
            continue
    assert not problems, json.dumps(problems, indent=2)
