"""Stored HTML injection through team form fields.

An anonymous client can POST /api/team/sync and /api/team/update. The team
forms endpoint returns action type, payment amount, time estimates, page
count, field count, and the other counts as stored. Each copy of the team
dashboard then assigns those strings to innerHTML.

These tests fail while that markup is parsed as HTML. They pass when every
stored value is shown as text, including inside edit inputs, and quote
characters cannot break out of the value attributes.

Run from the repository root, against PostgreSQL on localhost with an empty
password (local trust authentication):

    DB_HOST=127.0.0.1 DB_PORT=5432 DB_USER=formgenome DB_NAME=formgenome DB_PASSWORD= \\
      python3 -m pytest tests/security/test_stored_html_injection.py -q

Requires Google Chrome (CHROME_BIN overrides the executable) and Node.

Not covered here:
- dashboard/dashboard.js (and the copies under each extension folder) renders
  the same field names with innerHTML, but it reads browser.storage analysis
  records rather than /api/team/forms.
- form-genome-structured-cloudrun/ui/genetics.html fetches /api/records_unique,
  which is the same view, then calls forEach on the response object. The
  object has no forEach, so that page throws before it builds a table.
- complexity_score is an INTEGER column. A markup string fails the insert and
  is rolled back, so it is not an injection source. test_complexity_markup_is_not_stored
  locks that in.
"""

import json
import os
import pathlib
import subprocess
import sys

os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_USER", "formgenome")
os.environ.setdefault("DB_PASSWORD", "")
os.environ.setdefault("DB_NAME", "formgenome")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "form-genome-structured-cloudrun"
sys.path.insert(0, str(API_ROOT))

import pytest
from main import create_app

VICTIM_USER_ID = "victim-user"
VISIBLE_TITLE = "Visible Title Marker"
ESCAPED_ENTITY = '<div class="attacker-escaped-name"></div>'

# Markup an anonymous client stores. The edit payloads also contain a double
# quote so a fix that only escapes text content still fails in the value attribute.
SYNC_FIELDS = {
    "action_type": '<form class="attacker-action" action="https://attacker.example/login"></form>',
    "payment_amount": '<meta class="attacker-payment" http-equiv="refresh" content="99999;url=https://attacker.example/login">',
    "estimated_signer_time": '"><div class="attacker-edit-signer"></div>',
    "estimated_processing_time": '"><div class="attacker-edit-processing"></div>',
    "pages": '"><div class="attacker-edit-pages"></div>',
    "total_field_count": '"><div class="attacker-edit-fields"></div>',
    "nigo_score": '"><div class="attacker-edit-nigo"></div>',
    "signature_count": '"><div class="attacker-edit-sig"></div>',
    "attachment_count": '"><div class="attacker-edit-att"></div>',
}
SIG_ANALYSIS_MARKUP = '<div class="attacker-siganalysis"></div>'
UPDATED_ACTION = '<form class="attacker-updated-action" action="https://attacker.example/login"></form>'
UPDATED_PAYMENT = '<meta class="attacker-updated-payment" http-equiv="refresh" content="99999;url=https://attacker.example/login">'
USER_BREAKOUT = 'victim"><div class="attacker-userid"></div><span data-x="'
USER_ROW_URL = "https://attacker.example/userid-row"
SYNC_URL = "https://attacker.example/sync-row"
SIG_URL = "https://attacker.example/signature-analysis-row"
UPDATE_URL = "https://attacker.example/update-row"

INJECTED_CLASSES = [
    "attacker-action",
    "attacker-payment",
    "attacker-edit-signer",
    "attacker-edit-processing",
    "attacker-edit-pages",
    "attacker-edit-fields",
    "attacker-edit-nigo",
    "attacker-edit-sig",
    "attacker-edit-att",
    "attacker-siganalysis",
    "attacker-updated-action",
    "attacker-updated-payment",
    "attacker-userid",
]


def _pages():
    return sorted(
        str(path.relative_to(REPO_ROOT))
        for path in REPO_ROOT.glob("**/team-dashboard.html")
    )


@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        from app.services.storage import get_db

        with get_db() as conn:
            conn.cursor().execute("DELETE FROM forms")
    return app.test_client()


def _sync(client, user_id, source_url, form_name, extra):
    form_data = {
        "source_url": source_url,
        "form_name": form_name,
        "entity_name": "Example Entity",
        "complexity_score": 1,
    }
    form_data.update(extra)
    response = client.post(
        "/api/team/sync",
        json={"user_id": user_id, "form_data": form_data},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def _forms_by_url(client):
    response = client.get("/api/team/forms?limit=1000")
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body.get("ok") is True
    return body, {row["source_url"]: row for row in body["forms"]}


def test_complexity_markup_is_not_stored(client):
    """The INTEGER complexity column rejects markup and rolls the insert back."""
    response = client.post(
        "/api/team/sync",
        json={
            "user_id": VICTIM_USER_ID,
            "form_data": {
                "source_url": "https://attacker.example/complexity",
                "form_name": "Complexity probe",
                "complexity_score": '<div class="attacker-complexity"></div>',
                "action_type": '<div class="attacker-with-complexity"></div>',
            },
        },
    )
    assert response.status_code >= 400
    _, rows = _forms_by_url(client)
    assert "https://attacker.example/complexity" not in rows
    stored = json.dumps(list(rows.values()))
    assert "attacker-complexity" not in stored
    assert "attacker-with-complexity" not in stored


def test_anonymous_team_fields_render_as_text(client, tmp_path):
    _sync(
        client,
        VICTIM_USER_ID,
        SYNC_URL,
        VISIBLE_TITLE,
        {
            "entity_name": ESCAPED_ENTITY,
            **SYNC_FIELDS,
        },
    )
    _sync(
        client,
        VICTIM_USER_ID,
        SIG_URL,
        "Signature analysis row",
        {
            "signature_analysis": {"signature_count": SIG_ANALYSIS_MARKUP},
            "signature_count": "0",
        },
    )
    _sync(
        client,
        VICTIM_USER_ID,
        UPDATE_URL,
        "Update row",
        {"action_type": "sign", "payment_amount": "0"},
    )
    _sync(
        client,
        USER_BREAKOUT,
        USER_ROW_URL,
        "User id row",
        {"action_type": "sign"},
    )

    body, rows = _forms_by_url(client)
    synced = rows[SYNC_URL]
    for field, payload in SYNC_FIELDS.items():
        assert synced[field] == payload, field
    assert rows[SIG_URL]["signature_count"] == SIG_ANALYSIS_MARKUP
    assert rows[USER_ROW_URL]["user_id"] == USER_BREAKOUT

    update = client.post(
        "/api/team/update",
        json={
            "analysis_id": rows[UPDATE_URL]["analysis_id"],
            "form_data": {
                "action_type": UPDATED_ACTION,
                "payment_amount": UPDATED_PAYMENT,
            },
        },
    )
    assert update.status_code == 200, update.get_data(as_text=True)
    body, rows = _forms_by_url(client)
    assert rows[UPDATE_URL]["action_type"] == UPDATED_ACTION
    assert rows[UPDATE_URL]["payment_amount"] == UPDATED_PAYMENT

    text_payloads = list(SYNC_FIELDS.values()) + [
        SIG_ANALYSIS_MARKUP,
        UPDATED_ACTION,
        UPDATED_PAYMENT,
        ESCAPED_ENTITY,
        VISIBLE_TITLE,
        USER_ROW_URL,
    ]
    edit_values = dict(SYNC_FIELDS)

    config_path = tmp_path / "render-config.json"
    output_path = tmp_path / "render-result.json"
    config_path.write_text(
        json.dumps(
            {
                "pages": _pages(),
                "formsBody": body,
                "victimUserId": VICTIM_USER_ID,
                "editAnalysisId": rows[SYNC_URL]["analysis_id"],
                "outputPath": str(output_path),
            }
        )
    )
    completed = subprocess.run(
        [
            "node",
            str(REPO_ROOT / "tests/security/render_team_dashboard.mjs"),
            str(REPO_ROOT),
            str(config_path),
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    rendered = json.loads(output_path.read_text())
    assert rendered, "no dashboard pages were rendered"

    failures = []
    for page, result in sorted(rendered.items()):
        if result.get("error"):
            failures.append(f"{page}: {result['error']}")
            continue
        view = result["view"]
        edit = result["edit"]
        requests = view.get("requests") or []
        if not any("/api/team/forms?limit=1000" in url for url in requests):
            failures.append(f"{page}: did not request /api/team/forms?limit=1000 ({requests})")
        text = view.get("text") or ""
        for payload in text_payloads:
            if payload not in text:
                failures.append(f"{page}: markup was not text: {payload}")
        classes = set(view.get("classes") or [])
        classes.update(edit.get("classes") or [])
        leaked = sorted(classes.intersection(INJECTED_CLASSES))
        if leaked:
            failures.append(f"{page}: parsed injected elements {leaked}")
        if "attacker-escaped-name" in classes:
            failures.append(f"{page}: escaped entity name was parsed as an element")
        if not edit.get("clicked"):
            failures.append(f"{page}: Edit was not available for the victim's synced row")
        for field, payload in edit_values.items():
            actual = (edit.get("inputs") or {}).get(field)
            if actual != payload:
                failures.append(
                    f"{page}: edit field {field} value is {actual!r}, expected the stored string"
                )

    assert not failures, "\n".join(failures)
