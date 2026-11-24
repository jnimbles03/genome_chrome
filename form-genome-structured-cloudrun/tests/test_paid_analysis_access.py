"""Access control for paid analysis and batch endpoints.

These tests fail while the vulnerability is present. They pass once
POST /api/analyze/upload, POST /api/batch/create, and POST /api/team/reanalyze
stop doing paid work for callers who are not authenticated, and once the
server enforces the ceilings below.

Normative contract (the patch must satisfy this; request JSON cannot raise it):

- Authorization: Bearer tokens come from FORMGENOME_API_TOKENS, a
  comma-separated list of ``token:identity`` pairs. This file uses the
  placeholders ``placeholder-token-alice`` (identity alice) and
  ``placeholder-token-bob`` (identity bob). A missing header or an unknown
  token is rejected with 401 or 403.
- Rejection happens before any of: the Gemini generateContent HTTP call,
  a PDF download, a NamedTemporaryFile for the upload, form discovery, or
  a job_queue / batch_jobs insert.
- FORMGENOME_MAX_BATCH_URLS (10 here) is the most PDF URLs one create
  request may queue. Extra URLs are dropped or the request is rejected.
  Either way the queued count is strictly below the submitted count when
  the body is larger than the cap, and a one-URL batch from a valid token
  is still accepted.
- FORMGENOME_MAX_DISCOVERY_FORMS (20 here) is the most forms
  discover_forms_smart may be called with. The JSON max_forms value and
  the historical default of 5000 cannot exceed it. A valid token with
  max_forms at or below the cap still invokes discovery with that value.
- FORMGENOME_DAILY_UPLOADS (3 here) is how many PDF analyses each identity
  may run through /api/analyze/upload. The next one returns 429 and does
  not call Gemini. Another identity still has its own quota. The quota
  check and the paid call are one critical section: two requests that
  race for the last remaining slot produce one Gemini analysis, not two.

The Gemini transport is the installed google-genai client up to the socket.
``BaseApiClient._request_once`` is replaced so the test does not contact
generativelanguage.googleapis.com. Everything before that method, including
building the generateContent request from the PDF text, still runs.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest
import requests

from app.services.storage import get_db, init_db

MAX_BATCH_URLS = 10
MAX_DISCOVERY_FORMS = 20
DAILY_UPLOADS = 3
MARKER = "ATTACKER_FIELD_MARKER_SIGNATURE"
ALICE = "placeholder-token-alice"
BOB = "placeholder-token-bob"

SCOUT_AND_ANALYST_JSON = json.dumps(
    {
        "complexity_score": 80,
        "signature_required": True,
        "total_field_count": 40,
        "title": "Member Application",
        "entity": "Example Credit Union",
    }
)


def _pdf_bytes(text: str) -> bytes:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        (b"<< /Length %d >>\nstream\n" % len(stream)) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


PDF_BYTES = _pdf_bytes(MARKER)


class _PdfHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PDF_BYTES
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class GeminiTransport:
    """Stand-in for the socket write inside google-genai.

    ``Models.generate_content`` still builds the request. Only
    ``BaseApiClient._request_once`` (the method that calls httpx) returns a
    Gemini generateContent JSON body with ``candidates[].content.parts[].text``.
    """

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.delay = 0.0
        self._orig = None

    def install(self):
        from google.genai import _api_client

        self._HttpResponse = _api_client.HttpResponse
        self._orig = _api_client.BaseApiClient._request_once

        def _fake(client, http_request, stream=False):
            summary = _summarize_request(http_request)
            with self.lock:
                self.calls.append(summary)
            if self.delay:
                import time

                time.sleep(self.delay)
            payload = {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": SCOUT_AND_ANALYST_JSON}],
                        },
                        "finishReason": "STOP",
                    }
                ]
            }
            return self._HttpResponse({}, [json.dumps(payload)])

        _api_client.BaseApiClient._request_once = _fake

    def restore(self):
        if self._orig is not None:
            from google.genai import _api_client

            _api_client.BaseApiClient._request_once = self._orig

    def clear(self):
        with self.lock:
            self.calls.clear()

    def snapshot(self):
        with self.lock:
            return list(self.calls)


def _summarize_request(http_request) -> dict:
    data = http_request.data
    blob = json.dumps(data) if not isinstance(data, str) else data
    headers = {str(key).lower(): value for key, value in http_request.headers.items()}
    config = {}
    if isinstance(data, dict):
        config = data.get("generationConfig") or data.get("generation_config") or {}
    return {
        "generate_content": "generateContent" in (http_request.url or ""),
        "max_output_tokens": config.get("maxOutputTokens", config.get("max_output_tokens")),
        "marker": MARKER in blob,
        "api_key_header": "x-goog-api-key" in headers,
    }


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _count(table: str) -> int:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) AS n FROM {table}")
        return int(cursor.fetchone()["n"])


def _truncate():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("TRUNCATE job_queue, batch_jobs, forms RESTART IDENTITY")


def _form_id(url: str) -> int:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM forms WHERE url = %s", (url,))
        row = cursor.fetchone()
        if not row:
            raise AssertionError(f"seed form missing for {url}")
        return int(row["id"])


@pytest.fixture(scope="session")
def transport():
    gate = GeminiTransport()
    gate.install()
    yield gate
    gate.restore()


@pytest.fixture
def app(transport):
    from app.core.config import Config
    from app.services import llm_router
    from main import create_app

    Config.GEMINI_API_KEY = "placeholder-gemini-key"
    Config.GEMINI_RPM = 10000
    Config.ENABLE_LLM_RATE_LIMITING = True
    llm_router._gemini_rate_limiter = None
    transport.delay = 0.0
    transport.clear()

    application = create_app()
    application.config["TESTING"] = True
    init_db()
    _truncate()
    yield application
    transport.delay = 0.0
    transport.clear()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def pdf_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PdfHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _upload_file(client, token=None):
    headers = _bearer(token) if token else {}
    return client.post(
        "/api/analyze/upload",
        data={"file": (BytesIO(PDF_BYTES), "member-application.pdf")},
        content_type="multipart/form-data",
        headers=headers,
    )


def _post_json(client, path, payload, token=None):
    headers = _bearer(token) if token else {}
    return client.post(path, json=payload, headers=headers)


def test_upload_rejects_anonymous_before_provider_and_tempfile(client, transport, pdf_server, monkeypatch):
    """A valid token can analyze a PDF. Anonymous callers and unknown tokens cannot."""
    import app.api.analyze as analyze_mod

    created = []
    real_temp = analyze_mod.tempfile.NamedTemporaryFile

    def _counting_temp(*args, **kwargs):
        created.append(kwargs.get("suffix"))
        return real_temp(*args, **kwargs)

    monkeypatch.setattr(analyze_mod.tempfile, "NamedTemporaryFile", _counting_temp)

    downloads = []
    real_get = requests.get

    def _counting_get(url, *args, **kwargs):
        downloads.append(url)
        return real_get(url, *args, **kwargs)

    monkeypatch.setattr(requests, "get", _counting_get)

    allowed = _upload_file(client, ALICE)
    allowed_calls = transport.snapshot()
    transport.clear()
    created.clear()
    downloads.clear()

    anonymous_file = _upload_file(client, None)
    anonymous_file_calls = transport.snapshot()
    anonymous_file_temps = list(created)
    transport.clear()
    created.clear()

    anonymous_url = _post_json(client, "/api/analyze/upload", {"url": f"{pdf_server}/member-application.pdf"})
    anonymous_url_calls = transport.snapshot()
    anonymous_url_downloads = list(downloads)
    anonymous_url_temps = list(created)
    transport.clear()
    created.clear()
    downloads.clear()

    unknown = _upload_file(client, "placeholder-token-unknown")
    unknown_calls = transport.snapshot()
    unknown_temps = list(created)

    tokens = [call["max_output_tokens"] for call in allowed_calls]
    problems = []
    if allowed.status_code != 200:
        problems.append(f"alice upload status {allowed.status_code}")
    if tokens != [600, 4000]:
        problems.append(f"alice gemini maxOutputTokens {tokens}")
    if not allowed_calls or not all(call["marker"] and call["generate_content"] and call["api_key_header"] for call in allowed_calls):
        problems.append("alice upload did not put the PDF text on a generateContent request")
    if anonymous_file.status_code not in (401, 403):
        problems.append(f"anonymous file upload status {anonymous_file.status_code}")
    if anonymous_file_calls or anonymous_file_temps:
        problems.append(
            f"anonymous file upload billed gemini={len(anonymous_file_calls)} temps={anonymous_file_temps}"
        )
    if anonymous_url.status_code not in (401, 403):
        problems.append(f"anonymous url upload status {anonymous_url.status_code}")
    if anonymous_url_calls or anonymous_url_downloads or anonymous_url_temps:
        problems.append(
            "anonymous url upload performed work "
            f"gemini={len(anonymous_url_calls)} downloads={len(anonymous_url_downloads)} temps={len(anonymous_url_temps)}"
        )
    if unknown.status_code not in (401, 403) or unknown_calls or unknown_temps:
        problems.append(
            f"unknown token status {unknown.status_code} gemini={len(unknown_calls)} temps={len(unknown_temps)}"
        )
    assert not problems, "; ".join(problems)


def test_upload_quota_per_identity(client, transport):
    """Three analyses per identity, then 429 with no further Gemini call. Bob is separate."""
    statuses = []
    for index in range(DAILY_UPLOADS):
        response = _upload_file(client, ALICE)
        statuses.append(response.status_code)
    after_quota = len(transport.snapshot())
    overflow = _upload_file(client, ALICE)
    after_overflow = len(transport.snapshot())
    bob = _upload_file(client, BOB)

    problems = []
    if statuses != [200, 200, 200]:
        problems.append(f"alice statuses {statuses}")
    if after_quota != DAILY_UPLOADS * 2:
        problems.append(f"expected {DAILY_UPLOADS * 2} gemini calls in quota, saw {after_quota}")
    if overflow.status_code != 429 or after_overflow != after_quota:
        problems.append(
            f"overflow status {overflow.status_code}, gemini calls {after_quota} -> {after_overflow}"
        )
    if bob.status_code != 200 or len(transport.snapshot()) <= after_overflow:
        problems.append(f"bob status {bob.status_code} did not receive a separate quota")
    assert not problems, "; ".join(problems)


def test_upload_quota_is_atomic_under_concurrency(client, transport, app):
    """Two uploads racing for the last slot must bill only one analysis."""
    for _ in range(DAILY_UPLOADS - 1):
        response = _upload_file(client, ALICE)
        assert response.status_code == 200, response.status_code

    transport.clear()
    transport.delay = 0.4
    results = []

    def _send(name):
        local = app.test_client()
        response = local.post(
            "/api/analyze/upload",
            data={"file": (BytesIO(PDF_BYTES), name)},
            content_type="multipart/form-data",
            headers=_bearer(ALICE),
        )
        results.append(response.status_code)

    threads = [
        threading.Thread(target=_send, args=("race-a.pdf",)),
        threading.Thread(target=_send, args=("race-b.pdf",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    transport.delay = 0.0
    call_count = len(transport.snapshot())
    accepted = sum(1 for status in results if status == 200)
    problems = []
    if len(results) != 2:
        problems.append(f"race threads returned {results}")
    # One analysis is scout (600) plus deep (4000): exactly two provider calls.
    if call_count != 2 or accepted != 1:
        problems.append(f"race billed calls={call_count} accepted={accepted} statuses={results}")
    assert not problems, "; ".join(problems)


def _exercise_queued_job(transport):
    """Run the worker's rate-limit acquire and process_job if a job was queued."""
    from app.services.batch_processor import BatchConfig, RateLimiter, process_job
    from app.services.storage import get_next_job

    job = get_next_job("security-test")
    if not job:
        return "no-job"
    limiter = RateLimiter(requests_per_minute=30)
    acquired = limiter.acquire(timeout=1.0)
    before = len(transport.snapshot())
    error = None
    try:
        process_job(job, BatchConfig(pdf_timeout=5.0, rate_limit_enabled=False))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    after = transport.snapshot()
    new_calls = after[before:]
    tokens = [call["max_output_tokens"] for call in new_calls]
    return f"acquired={acquired} new_gemini={len(new_calls)} tokens={tokens} error={error}"


def test_batch_create_rejects_anonymous_and_worker_would_bill(client, transport, pdf_server):
    """Anonymous batch create must not queue jobs. A valid one-URL batch still can."""
    urls = [f"{pdf_server}/form-{index}.pdf" for index in range(MAX_BATCH_URLS + 30)]
    anonymous = _post_json(client, "/api/batch/create", {"urls": urls})
    queued = _count("job_queue")
    batches = _count("batch_jobs")
    worker = _exercise_queued_job(transport) if queued else "not-run"
    billed = transport.snapshot()

    _truncate()
    transport.clear()
    allowed = _post_json(
        client,
        "/api/batch/create",
        {"urls": [f"{pdf_server}/one-form.pdf"]},
        ALICE,
    )
    allowed_jobs = _count("job_queue")

    unknown = _post_json(
        client,
        "/api/batch/create",
        {"urls": [f"{pdf_server}/unknown-token.pdf"]},
        "placeholder-token-unknown",
    )

    problems = []
    if anonymous.status_code not in (401, 403):
        problems.append(f"anonymous status {anonymous.status_code} body={anonymous.get_json()}")
    if queued or batches or billed:
        problems.append(
            f"anonymous create queued_jobs={queued} batches={batches} "
            f"worker={worker} gemini={len(billed)}"
        )
    if allowed.status_code not in (200, 201) or allowed_jobs != 1:
        problems.append(f"alice one-url status {allowed.status_code} jobs={allowed_jobs}")
    if unknown.status_code not in (401, 403) or _count("job_queue") != allowed_jobs:
        problems.append(
            f"unknown token status {unknown.status_code} jobs={_count('job_queue')}"
        )
    assert not problems, "; ".join(problems)


def test_batch_create_caps_urls_per_request(client, pdf_server):
    """One request cannot queue more URLs than FORMGENOME_MAX_BATCH_URLS."""
    urls = [f"{pdf_server}/capped-{index}.pdf" for index in range(MAX_BATCH_URLS + 30)]
    response = _post_json(client, "/api/batch/create", {"urls": urls}, ALICE)
    queued = _count("job_queue")
    problems = []
    if queued > MAX_BATCH_URLS or queued >= len(urls):
        problems.append(
            f"queued {queued} of {len(urls)} (cap {MAX_BATCH_URLS}) status={response.status_code}"
        )
    assert not problems, "; ".join(problems)


def test_batch_discovery_requires_auth_and_caps_max_forms(client, monkeypatch):
    """Discovery is authenticated, and max_forms cannot exceed the server ceiling."""
    import app.api.batch as batch_mod

    seen = []

    def _spy(url, max_forms=200, strategy="auto"):
        seen.append({"url": url, "max_forms": max_forms, "strategy": strategy})
        return []

    monkeypatch.setattr(batch_mod, "discover_forms_smart", _spy)

    anonymous = _post_json(
        client,
        "/api/batch/create",
        {"url": "http://127.0.0.1/seed", "max_forms": 5},
    )
    anonymous_seen = list(seen)
    seen.clear()

    unknown = _post_json(
        client,
        "/api/batch/create",
        {"url": "http://127.0.0.1/seed", "max_forms": 5},
        "placeholder-token-unknown",
    )
    unknown_seen = list(seen)
    seen.clear()

    huge = _post_json(
        client,
        "/api/batch/create",
        {"url": "http://127.0.0.1/seed", "max_forms": 5000},
        ALICE,
    )
    huge_seen = list(seen)
    seen.clear()

    defaulted = _post_json(
        client,
        "/api/batch/create",
        {"url": "http://127.0.0.1/seed"},
        ALICE,
    )
    default_seen = list(seen)
    seen.clear()

    small = _post_json(
        client,
        "/api/batch/create",
        {"url": "http://127.0.0.1/seed", "max_forms": 5},
        ALICE,
    )
    small_seen = list(seen)

    problems = []
    if anonymous.status_code not in (401, 403) or anonymous_seen:
        problems.append(f"anonymous discovery status {anonymous.status_code} calls={anonymous_seen}")
    if unknown.status_code not in (401, 403) or unknown_seen:
        problems.append(f"unknown token discovery status {unknown.status_code} calls={unknown_seen}")
    if any(call["max_forms"] > MAX_DISCOVERY_FORMS for call in huge_seen):
        problems.append(f"max_forms 5000 forwarded as {huge_seen}")
    if huge.status_code in (401, 403):
        problems.append("alice was rejected for an authenticated discovery request")
    if any(call["max_forms"] > MAX_DISCOVERY_FORMS for call in default_seen):
        problems.append(f"default max_forms forwarded as {default_seen}")
    if not small_seen or small_seen[0]["max_forms"] != 5:
        problems.append(f"max_forms 5 was not forwarded unchanged: {small_seen} status={small.status_code}")
    assert not problems, "; ".join(problems)


def test_reanalyze_requires_auth_before_queue(client, transport, pdf_server):
    """Team reanalyze queues the same paid worker path and needs the same auth."""
    from app.services.storage import save_form

    source = f"{pdf_server}/reanalyze.pdf"
    save_form(source, "Member Application", "Example Credit Union", 10, {"seed": True})
    form_id = _form_id(source)

    allowed = _post_json(client, "/api/team/reanalyze", {"analysis_ids": [form_id]}, ALICE)
    allowed_jobs = _count("job_queue")

    _truncate()
    save_form(source, "Member Application", "Example Credit Union", 10, {"seed": True})
    form_id = _form_id(source)
    transport.clear()

    anonymous = _post_json(client, "/api/team/reanalyze", {"analysis_ids": [form_id]})
    queued = _count("job_queue")
    worker = _exercise_queued_job(transport) if queued else "not-run"
    billed = transport.snapshot()

    _truncate()
    save_form(source, "Member Application", "Example Credit Union", 10, {"seed": True})
    form_id = _form_id(source)
    unknown = _post_json(
        client,
        "/api/team/reanalyze",
        {"analysis_ids": [form_id]},
        "placeholder-token-unknown",
    )

    problems = []
    if allowed.status_code != 200 or allowed_jobs != 1:
        problems.append(f"alice reanalyze status {allowed.status_code} jobs={allowed_jobs} body={allowed.get_json()}")
    if anonymous.status_code not in (401, 403) or queued or billed:
        problems.append(
            f"anonymous reanalyze status {anonymous.status_code} jobs={queued} worker={worker} gemini={len(billed)}"
        )
    if unknown.status_code not in (401, 403) or _count("job_queue") != 0:
        problems.append(f"unknown token reanalyze status {unknown.status_code} jobs={_count('job_queue')}")
    assert not problems, "; ".join(problems)
