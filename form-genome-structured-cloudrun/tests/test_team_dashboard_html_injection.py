"""Team dashboard HTML injection.

These tests describe the safe behavior. They fail while server-supplied team
fields are written into the dashboard with innerHTML or unescaped attributes.

Run from form-genome-structured-cloudrun, against a local PostgreSQL database
named formgenome (the test sets DB_* to 127.0.0.1 / user formgenome / password
test / database formgenome):

    python3 -m pytest tests/test_team_dashboard_html_injection.py -q

The browser half needs Playwright 1.40 and Google Chrome at
/usr/local/bin/google-chrome.
"""

import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

os.environ["DB_HOST"] = "127.0.0.1"
os.environ["DB_PORT"] = "5432"
os.environ["DB_USER"] = "formgenome"
os.environ["DB_PASSWORD"] = "test"
os.environ["DB_NAME"] = "formgenome"

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from main import create_app  # noqa: E402
from app.services.storage import get_db, save_form  # noqa: E402

API_HOSTS = (
    "https://form-genome-483910792736.us-central1.run.app",
    "https://form-genome-api-nxqo233ggq-uc.a.run.app",
)
CHROME = "/usr/local/bin/google-chrome"
VIEWER_ID = "victim-user"

# Prefix is plain text so a fix that escapes markup or strips tags still shows
# it. The selector is an element that only exists when the value is parsed as HTML.
DISPLAY_PROBES = (
    {
        "field": "action_type",
        "payload": '<meta http-equiv="refresh" content="60;url=http://127.0.0.1:9/phish-action"><span id="inj-action">x</span>',
        "token": "action-token",
        "selector": "#inj-action, meta[http-equiv='refresh']",
    },
    {
        "field": "payment_amount",
        "payload": '<form id="inj-payment" action="http://127.0.0.1:9/collect"><input name="secret"></form>',
        "token": "payment-token",
        "selector": "#inj-payment, form",
    },
    {
        "field": "estimated_signer_time",
        "payload": '<a id="inj-signer" href="http://127.0.0.1:9/signer">go</a>',
        "token": "signer-token",
        "selector": "#inj-signer",
    },
    {
        "field": "estimated_processing_time",
        "payload": '<img id="inj-proc" src="http://127.0.0.1:9/proc.png" alt="p">',
        "token": "proc-token",
        "selector": "#inj-proc",
    },
    {
        "field": "pages",
        "payload": '<iframe id="inj-pages" src="http://127.0.0.1:9/pages"></iframe>',
        "token": "pages-token",
        "selector": "#inj-pages",
    },
    {
        "field": "total_field_count",
        "payload": '<svg id="inj-fields"><title>t</title></svg>',
        "token": "fields-token",
        "selector": "#inj-fields",
    },
    {
        "field": "nigo_score",
        "payload": '<div id="inj-nigo">n</div>',
        "token": "nigo-token",
        "selector": "#inj-nigo",
    },
    {
        "field": "signature_count",
        "payload": '<object id="inj-sigs" data="http://127.0.0.1:9/sig"></object>',
        "token": "sigs-token",
        "selector": "#inj-sigs",
    },
    {
        "field": "attachment_count",
        "payload": '<embed id="inj-atts" src="http://127.0.0.1:9/att">',
        "token": "atts-token",
        "selector": "#inj-atts",
    },
)

# Quote breakout for edit-mode value attributes. No "<", so display mode does
# not already create an element before Edit is clicked.
EDIT_PROBES = (
    {"field": "action_type", "payload": 'action-edit" onfocus="window.__pwned=1"', "handler": "onfocus"},
    {"field": "payment_amount", "payload": 'payment-edit" onclick="window.__pwned=1"', "handler": "onclick"},
    {"field": "estimated_signer_time", "payload": 'signer-edit" onmouseover="window.__pwned=1"', "handler": "onmouseover"},
    {"field": "estimated_processing_time", "payload": 'proc-edit" onmouseenter="window.__pwned=1"', "handler": "onmouseenter"},
    {"field": "pages", "payload": '8" onfocus="window.__pwned=1"', "handler": "onfocus"},
    {"field": "total_field_count", "payload": '9" onclick="window.__pwned=1"', "handler": "onclick"},
    {"field": "nigo_score", "payload": '7" onmouseover="window.__pwned=1"', "handler": "onmouseover"},
    {"field": "signature_count", "payload": '4" onfocus="window.__pwned=1"', "handler": "onfocus"},
    {"field": "attachment_count", "payload": '3" onclick="window.__pwned=1"', "handler": "onclick"},
)
TEXT_EDIT_FIELDS = {
    "action_type",
    "payment_amount",
    "estimated_signer_time",
    "estimated_processing_time",
}

USER_ID_PAYLOAD = 'zz"><span id="inj-userid">user-token</span>'

DASHBOARD_PAGES = (
    "dashboard/team-dashboard.html",
    "extracted_chrome_share/dashboard/team-dashboard.html",
    "FormGenome_Extension_Final_Fixed/dashboard/team-dashboard.html",
    "FormGenome_Extension_Firefox/dashboard/team-dashboard.html",
    "FormGenome_Extension_Normalization_Fix/dashboard/team-dashboard.html",
    "FormGenome_Extension_Data_Fix/dashboard/team-dashboard.html",
    "FormGenome_Extension_Final_Debug/dashboard/team-dashboard.html",
    "FormGenome_Extension_Batch_Fix/dashboard/team-dashboard.html",
    "FormGenome_Extension_Clean/dashboard/team-dashboard.html",
)

HANDLER_SELECTOR = "[onfocus],[onclick],[onmouseover],[onmouseenter]"


def _with_tokens(probes):
    """Put the visible token before the markup so stripping tags leaves it."""
    prepared = []
    for probe in probes:
        item = dict(probe)
        if not item["payload"].startswith(item.get("token", "")):
            if "token" in item:
                item["payload"] = item["token"] + item["payload"]
        prepared.append(item)
    return prepared


DISPLAY_PROBES = tuple(_with_tokens(DISPLAY_PROBES))


def _benign_form(source_url, **overrides):
    form = {
        "form_name": "Benefits Enrollment",
        "entity_name": "Example Agency",
        "source_url": source_url,
        "complexity_score": 3,
        "nigo_score": 2,
        "pages": 4,
        "total_field_count": 12,
        "confidence_tier": "High",
        "action_type": "Signature Required",
        "signature_required": False,
        "signature_count": 1,
        "notarization_required": False,
        "attachments_required": False,
        "attachment_count": 0,
        "payment_required": False,
        "payment_amount": "$25.00",
        "identification_required": False,
        "conditional_logic": False,
        "third_party_involved": False,
        "witnesses_required": False,
        "deadlines_present": False,
        "form_purpose": "Enroll a member",
        "industry_vertical": "Healthcare",
        "industry_subvertical": "Benefits",
        "estimated_signer_time": "15 minutes",
        "estimated_processing_time": "5 days",
    }
    form.update(overrides)
    return form


def _sync(client, source_url, user_id=VIEWER_ID, **overrides):
    return client.post(
        "/api/team/sync",
        json={
            "user_id": user_id,
            "analysis_id": "analysis_local",
            "form_data": _benign_form(source_url, **overrides),
        },
    )


def _listed_forms(client):
    response = client.get("/api/team/forms?limit=1000")
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    return body["forms"]


def _metadata_blobs():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT url, title, metadata FROM forms")
        rows = cursor.fetchall()
    blobs = []
    for row in rows:
        meta = row["metadata"]
        if isinstance(meta, dict):
            meta = json.dumps(meta)
        blobs.append((row["url"], row["title"] or "", meta or ""))
    return blobs


@pytest.fixture(scope="session")
def app():
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM forms")
    return app.test_client()


@pytest.fixture(scope="session")
def api_server(app):
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()


@pytest.fixture(scope="session")
def static_server():
    handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(  # noqa: E731
        *args, directory=str(REPO_ROOT), **kwargs
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        launched = playwright.chromium.launch(
            executable_path=CHROME,
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        try:
            yield launched
        finally:
            launched.close()


@pytest.fixture
def page(browser, api_server):
    context = browser.new_context()
    tab = context.new_page()
    tab.add_init_script(
        """
        const store = { userId: %s };
        const storage = {
          local: {
            get(key) {
              let value;
              if (key == null) value = Object.assign({}, store);
              else if (typeof key === 'string') value = { [key]: store[key] };
              else if (Array.isArray(key)) {
                value = {};
                key.forEach((k) => { value[k] = store[k]; });
              } else {
                value = {};
                Object.keys(key).forEach((k) => {
                  value[k] = store[k] !== undefined ? store[k] : key[k];
                });
              }
              return Promise.resolve(value);
            },
            set(obj) {
              Object.assign(store, obj);
              return Promise.resolve();
            }
          }
        };
        window.chrome = { storage, runtime: { getURL: (path) => path } };
        window.browser = window.chrome;
        """
        % json.dumps(VIEWER_ID)
    )
    tab.on("dialog", lambda dialog: dialog.accept())

    def proxy(route):
        request = route.request
        local = None
        for host in API_HOSTS:
            if request.url.startswith(host):
                local = "http://127.0.0.1:%d%s" % (api_server, request.url[len(host):])
                break
        if local is None or not local.startswith("http://127.0.0.1:"):
            route.abort()
            return
        data = request.post_data_buffer if request.method not in ("GET", "HEAD") else None
        headers = {}
        if data is not None:
            headers["Content-Type"] = request.headers.get("content-type", "application/json")
        outgoing = Request(local, data=data, headers=headers, method=request.method)
        try:
            with urlopen(outgoing, timeout=15) as response:
                body = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "application/json")
        except HTTPError as error:
            body = error.read()
            status = error.code
            content_type = error.headers.get("Content-Type", "application/json")
        route.fulfill(status=status, headers={"Content-Type": content_type}, body=body)

    for host in API_HOSTS:
        tab.route(host + "/**", proxy)
    try:
        yield tab
    finally:
        context.close()


def _open_dashboard(page, static_port, html_path):
    url = "http://127.0.0.1:%d/%s" % (static_port, html_path)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_function(
        """() => {
            const table = document.getElementById('formsTable');
            const empty = document.getElementById('emptyState');
            const tableVisible = table && table.style.display !== 'none';
            const emptyVisible = empty && empty.style.display !== 'none';
            return tableVisible || emptyVisible;
        }""",
        timeout=10000,
    )


def _seed_saved_form(source_url, user_id=VIEWER_ID, **overrides):
    form = _benign_form(source_url, **overrides)
    save_form(
        url=form["source_url"],
        title=form["form_name"],
        entity_name=form["entity_name"],
        complexity=form["complexity_score"] if isinstance(form["complexity_score"], int) else 1,
        metadata=dict(form, user_id=user_id),
    )
    return form


def test_benign_team_sync_is_stored_and_listed(client):
    response = _sync(client, "https://example.test/forms/benefits.pdf")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True

    forms = _listed_forms(client)
    assert len(forms) == 1
    stored = forms[0]
    assert stored["action_type"] == "Signature Required"
    assert stored["payment_amount"] == "$25.00"
    assert stored["estimated_signer_time"] == "15 minutes"
    assert stored["estimated_processing_time"] == "5 days"
    assert stored["pages"] == 4
    assert stored["total_field_count"] == 12
    assert stored["nigo_score"] == 2
    assert stored["signature_count"] == 1
    assert stored["attachment_count"] == 0
    assert stored["form_name"] == "Benefits Enrollment"
    assert stored["user_id"] == VIEWER_ID

    alias = client.get("/api/records_unique?limit=1000")
    assert alias.status_code == 200
    assert alias.get_json()["forms"][0]["action_type"] == "Signature Required"


@pytest.mark.parametrize(
    "field,value",
    [
        ("action_type", '<meta http-equiv="refresh" content="0;url=http://127.0.0.1:9/phish">'),
        ("payment_amount", '<form action="http://127.0.0.1:9/collect"><input name="secret"></form>'),
        ("estimated_signer_time", '<a href="http://127.0.0.1:9/signer">go</a>'),
        ("estimated_processing_time", '<img src="http://127.0.0.1:9/proc.png">'),
        ("pages", '<iframe src="http://127.0.0.1:9/pages"></iframe>'),
        ("total_field_count", '<div id="inj-fields">x</div>'),
        ("nigo_score", '<div id="inj-nigo">x</div>'),
        ("signature_count", '<object data="http://127.0.0.1:9/sig"></object>'),
        ("attachment_count", '1" onfocus="window.__pwned=1"'),
        ("action_type", 'Signature" onfocus="window.__pwned=1"'),
        ("payment_amount", "25' onclick='window.__pwned=1'"),
    ],
)
def test_sync_rejects_markup_without_persisting(client, field, value):
    source_url = "https://example.test/forms/%s-%s.pdf" % (field, abs(hash(value)))
    before = _metadata_blobs()
    response = _sync(client, source_url, **{field: value})

    assert response.status_code >= 400
    assert source_url not in {url for url, _title, _meta in _metadata_blobs()}
    assert _metadata_blobs() == before
    assert all(value not in blob for _url, _title, blob in _metadata_blobs())
    listed = client.get("/api/team/forms?limit=1000").get_json()["forms"]
    assert all(source_url != form["source_url"] for form in listed)


def test_update_rejects_markup_without_changing_the_stored_form(client):
    created = _sync(client, "https://example.test/forms/update-target.pdf")
    assert created.status_code == 200
    original = _listed_forms(client)[0]
    analysis_id = original["analysis_id"]

    malicious = dict(original)
    malicious["form_name"] = "Hijacked Name"
    malicious["action_type"] = '<span id="inj-update">update-token</span>'
    malicious["payment_amount"] = '20" onfocus="window.__pwned=1"'
    malicious["pages"] = '<iframe id="inj-update-pages"></iframe>'

    response = client.post(
        "/api/team/update",
        json={"analysis_id": analysis_id, "form_data": malicious},
    )
    assert response.status_code >= 400

    stored = _listed_forms(client)
    assert len(stored) == 1
    assert stored[0]["form_name"] == "Benefits Enrollment"
    assert stored[0]["action_type"] == "Signature Required"
    assert stored[0]["payment_amount"] == "$25.00"
    assert stored[0]["pages"] == 4
    blobs = _metadata_blobs()
    assert all("update-token" not in blob for _url, _title, blob in blobs)
    assert all("Hijacked Name" not in title for _url, title, _blob in blobs)


@pytest.mark.parametrize("html_path", DASHBOARD_PAGES)
def test_team_dashboard_renders_server_fields_as_text(client, page, static_server, html_path):
    fields = {probe["field"]: probe["payload"] for probe in DISPLAY_PROBES}
    fields["form_name"] = '<span id="inj-name">name-token</span>'
    _seed_saved_form("https://example.test/forms/display-%s.pdf" % html_path.split("/")[0], **fields)

    listed = _listed_forms(client)
    assert len(listed) == 1
    for probe in DISPLAY_PROBES:
        assert probe["token"] in str(listed[0][probe["field"]])

    _open_dashboard(page, static_server, html_path)
    body_text = page.locator("body").inner_text()
    assert "name-token" in body_text
    assert page.locator("#inj-name").count() == 0

    problems = []
    if "phish-action" in page.url:
        problems.append("meta refresh navigated the dashboard")
    if page.locator("meta[http-equiv='refresh']").count():
        problems.append("meta refresh element is in the document")
    for probe in DISPLAY_PROBES:
        if page.locator(probe["selector"]).count():
            problems.append("%s was parsed as HTML (%s)" % (probe["field"], probe["selector"]))
        if probe["token"] not in body_text:
            problems.append("%s token %s is not visible as text" % (probe["field"], probe["token"]))
    assert not problems, "; ".join(problems)


@pytest.mark.parametrize("html_path", DASHBOARD_PAGES)
def test_team_dashboard_edit_mode_keeps_values_inside_attributes(client, page, static_server, html_path):
    fields = {probe["field"]: probe["payload"] for probe in EDIT_PROBES}
    _seed_saved_form("https://example.test/forms/edit-%s.pdf" % html_path.split("/")[0], **fields)
    _open_dashboard(page, static_server, html_path)

    edit_buttons = page.locator(".edit-btn")
    assert edit_buttons.count() == 1
    edit_buttons.click()
    page.wait_for_selector("input[name='action_type']")

    state = page.evaluate(
        """(fields) => {
            const handlers = document.querySelectorAll(
                '[onfocus],[onclick],[onmouseover],[onmouseenter]'
            ).length;
            const inputs = {};
            fields.forEach((name) => {
                const input = document.querySelector(`input[name="${name}"]`);
                inputs[name] = input ? {
                    value: input.value,
                    attr: input.getAttribute('value'),
                    onfocus: input.getAttribute('onfocus'),
                    onclick: input.getAttribute('onclick'),
                    onmouseover: input.getAttribute('onmouseover'),
                    onmouseenter: input.getAttribute('onmouseenter'),
                } : null;
            });
            return { handlers, inputs, pwned: window.__pwned || null };
        }""",
        [probe["field"] for probe in EDIT_PROBES],
    )

    problems = []
    # Header labels use onclick for sorting. Only handlers on the edit inputs,
    # or a handler that actually ran, count as a breakout.
    if state["pwned"]:
        problems.append("edit markup ran an event handler")
    for probe in EDIT_PROBES:
        info = state["inputs"].get(probe["field"])
        if not info:
            problems.append("%s input missing" % probe["field"])
            continue
        if info.get(probe["handler"]):
            problems.append("%s broke out of the value attribute via %s" % (probe["field"], probe["handler"]))
        if probe["field"] in TEXT_EDIT_FIELDS and info["value"] != probe["payload"]:
            problems.append("%s value is %r, not the stored text" % (probe["field"], info["value"]))
        if probe["field"] not in TEXT_EDIT_FIELDS and info["attr"] != probe["payload"] and info["value"] != probe["payload"]:
            problems.append("%s attribute is %r" % (probe["field"], info["attr"]))
    assert not problems, "; ".join(problems)


@pytest.mark.parametrize("html_path", DASHBOARD_PAGES)
def test_team_dashboard_escapes_user_id_in_row_attributes(client, page, static_server, html_path):
    _seed_saved_form(
        "https://example.test/forms/userid-%s.pdf" % html_path.split("/")[0],
        user_id=USER_ID_PAYLOAD,
    )
    _open_dashboard(page, static_server, html_path)

    assert page.locator("#inj-userid").count() == 0
    row_ids = page.eval_on_selector_all(
        "[data-row-id]",
        "elements => elements.map((element) => element.getAttribute('data-row-id'))",
    )
    assert any(row_id and "user-token" in row_id for row_id in row_ids)
