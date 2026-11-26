"""Stored XSS in the downloadable HTML report.

The tests follow the extension's analyze path: a PDF's extracted text is placed
in the real scout/analyst prompt, the completion is parsed by analyze_pdf_file,
and generateBatchReport writes the returned title and entity into an HTML file.
They fail while that file runs the text as markup. They pass once every dynamic
report value is HTML-escaped and still visible as text.

Run from the repository root:

    python3 -m pytest tests/security/test_report_stored_xss.py -q
    node tests/security/report_xss_harness.js

The node command alone checks the report template and the popup list. The pytest
command also feeds titles and entities through the analyze upload endpoint.
"""

import json
import re
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLOUD = REPO / "form-genome-structured-cloudrun"
sys.path.insert(0, str(CLOUD))

from flask import Flask  # noqa: E402

import app.api.analyze as analyze  # noqa: E402
HARNESS = REPO / "tests" / "security" / "report_xss_harness.js"

# These strings are the document header and the organisation name inside the PDF.
# They are not passed to the stand-in directly; the stand-in copies them out of
# the prompt analyze_pdf_file builds.
TITLE_TEXT = "<img src=x onerror=document.documentElement.dataset.xssTitle=1>"
ENTITY_TEXT = "</title><img src=x onerror=document.documentElement.dataset.xssEntity=1>"
PDF_URL = "https://attacker.example/forms/member.pdf"
KNOWN_DOMAIN_URL = "https://www.becu.org/forms/member.pdf"


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(pages):
    """Minimal PDF whose text operators are the given page strings.

    pypdf 3.17.4 extract_text reads Tj literal strings back out. Parentheses
    are escaped so the payload round-trips.
    """
    page_ids = []
    next_id = 4
    parts = []

    def add(number, body: bytes):
        parts.append((number, body))

    for lines in pages:
        ops = []
        y = 720
        for line in lines:
            ops.append(f"BT /F1 18 Tf 72 {y} Td ({_pdf_escape(line)}) Tj ET")
            y -= 28
        stream = "\n".join(ops).encode("latin-1")
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_ids.append((page_id, content_id, stream))

    kids = " ".join(f"{page_id} 0 R" for page_id, _, _ in page_ids)
    add(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add(2, f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    add(3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for page_id, content_id, stream in page_ids:
        add(
            page_id,
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_id} 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
            ).encode(),
        )
        add(content_id, b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number, body in parts:
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    max_id = max(offsets)
    out += f"xref\n0 {max_id + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for number in range(1, max_id + 1):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += (
        f"trailer << /Size {max_id + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def page_bodies(user_message: str) -> dict:
    """Split the document section of the real scout/analyst user prompt.

    Both prompts end with a line 'Return JSON:' after the extracted text
    (prompts.py SCOUT_USER_PROMPT and ANALYST_USER_PROMPT).
    """
    bodies = {}
    current = None
    buf = []
    for line in user_message.splitlines():
        marker = re.fullmatch(r"--- PAGE (\d+) ---", line.strip())
        if marker:
            if current is not None:
                bodies[current] = "\n".join(buf).strip()
            current = int(marker.group(1))
            buf = []
            continue
        if current is None:
            continue
        if line.strip() == "Return JSON:":
            break
        buf.append(line)
    if current is not None:
        bodies[current] = "\n".join(buf).strip()
    return bodies


class ExtractionCompletion:
    """Stand-in for chat_complete.

    analyze_pdf_file asks the model for JSON with title and entity taken from
    the document text. This returns that JSON by copying the page text the
    real prompt builder just inserted. It does not add characters that were
    not in the prompt. Modes select the scout-only, deep, and malformed-JSON
    paths inside analyze_pdf_file.
    """

    def __init__(self, mode: str):
        self.mode = mode
        self.calls = []

    def __call__(self, messages, max_tokens=1000, **kwargs):
        user = next(item["content"] for item in messages if item.get("role") == "user")
        self.calls.append({"max_tokens": max_tokens, "user": user})
        bodies = page_bodies(user)
        title = bodies.get(1, "")
        entity = bodies.get(2, "")
        if self.mode == "scout_simple":
            return json.dumps({
                "title": title,
                "entity": entity,
                "complexity_score": 1,
                "total_field_count": 1,
                "signature_count": 0,
                "signature_required": False,
                "notarization_required": False,
                "payment_required": False,
                "confidence_tier": "High",
            })
        if self.mode == "deep":
            if max_tokens <= 600:
                return json.dumps({
                    "title": "SCOUT_SHOULD_NOT_APPEAR",
                    "entity": "Scout Entity",
                    "complexity_score": 80,
                    "total_field_count": 40,
                    "signature_count": 2,
                    "signature_required": True,
                    "confidence_tier": "Low",
                })
            return json.dumps({
                "title": title,
                "entity": entity,
                "complexity_score": 80,
                "total_field_count": 40,
                "signature_count": 2,
                "signature_required": True,
                "notarization_required": False,
                "payment_required": False,
                "confidence_tier": "High",
            })
        if self.mode == "regex":
            if max_tokens <= 600:
                return json.dumps({
                    "title": "SCOUT_SHOULD_NOT_APPEAR",
                    "entity": "Scout Entity",
                    "complexity_score": 80,
                    "total_field_count": 40,
                    "signature_required": True,
                    "confidence_tier": "Low",
                })
            if max_tokens == 4000:
                return "the model did not return json"
            # Trailing comma is invalid JSON, so analyze_pdf_file takes the
            # regex extraction branch for title and entity.
            return (
                '{ "title": "' + title + '", "entity": "' + entity +
                '", "total_field_count": 2, "signature_required": false, }'
            )
        raise AssertionError("unknown mode " + self.mode)


def extracted_pages(pdf_bytes: bytes):
    from pypdf import PdfReader
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(pdf_bytes)
        handle.flush()
        reader = PdfReader(handle.name)
        return [analyze.clean_text(page.extract_text() or "") for page in reader.pages]


def upload(pdf_bytes: bytes, mode: str, source_url: str = None):
    echo = ExtractionCompletion(mode)
    analyze.chat_complete = echo
    app = Flask(__name__)
    app.config["TESTING"] = True
    analyze.register_analyze_api(app)
    client = app.test_client()
    form = {"file": (BytesIO(pdf_bytes), "member-application.pdf")}
    if source_url:
        form["url"] = source_url
    response = client.post("/api/analyze/upload", data=form, content_type="multipart/form-data")
    return response, echo


def assert_prompt_contained_document(echo, title, entity):
    assert echo.calls, "analyze_pdf_file never called the model"
    assert any(title in call["user"] and entity in call["user"] for call in echo.calls), (
        "PDF text did not reach the model prompt unchanged"
    )


@pytest.fixture(scope="module")
def pdf_bytes():
    return build_pdf([[TITLE_TEXT], [ENTITY_TEXT]])


@pytest.fixture(scope="module")
def page_text(pdf_bytes):
    pages = extracted_pages(pdf_bytes)
    assert pages[0] == TITLE_TEXT
    assert pages[1] == ENTITY_TEXT
    return pages


def test_report_escapes_titles_and_entities_from_the_analyze_path(pdf_bytes, page_text, tmp_path):
    cases = [
        ("scout-upload", "scout_simple", None),
        ("scout-known-domain", "scout_simple", KNOWN_DOMAIN_URL),
        ("deep", "deep", PDF_URL),
        ("regex-fallback", "regex", PDF_URL),
    ]
    for name, mode, source_url in cases:
        response, echo = upload(pdf_bytes, mode, source_url)
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        result = body["results"]
        assert result.get("success") is True
        assert not result.get("error")
        assert_prompt_contained_document(echo, page_text[0], page_text[1])
        if mode == "deep":
            assert any(call["max_tokens"] == 4000 for call in echo.calls)
        if mode == "regex":
            assert any(call["max_tokens"] == 8192 for call in echo.calls)
        fixture = {
            "name": name,
            "pdfUrl": PDF_URL,
            "pdfText": "Member form",
            "languageCount": 1,
            "result": result,
            "expectTitle": page_text[0],
            "expectEntity": page_text[1],
        }
        (tmp_path / f"{name}.json").write_text(json.dumps(fixture))

    completed = subprocess.run(
        ["node", str(HARNESS), str(tmp_path)],
        cwd=str(REPO),
        text=True,
        capture_output=True,
        timeout=180,
    )
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    assert completed.returncode == 0, completed.stdout + completed.stderr
