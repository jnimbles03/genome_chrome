"""Security contract for public LLM-backed analysis and batch creation.

These tests fail while anonymous callers can drive Gemini and queue batch
jobs. They pass once the application enforces the contract below. They are
expected to fail until that fix lands.

Contract
--------
POST /api/analyze/upload and POST /api/batch/create require
``Authorization: Bearer <token>``. Accepted tokens are the comma-separated
values of FORMGENOME_API_TOKENS (these tests set two placeholders,
``client-a-token`` and ``client-b-token``). A missing or unknown token is
401 or 403.

Refusal happens before any side effect: no PDF download, no temporary file,
no Gemini HTTP call, and no job_queue or batch_jobs insert.

A token is one client. In a single process, one client may cause at most
MAX_GEMINI_CALLS_PER_CLIENT (6) successful ``models/*:generateContent``
requests per minute. The next request from that token is 429 and performs
no further generateContent call, including when it comes from a different
source IP. A second token that still has budget receives 200 and its own
generateContent calls, even from the same source IP. The process-wide
Gemini RPM limiter is not a substitute for this per-client budget.

POST /api/batch/create with a ``urls`` list longer than MAX_BATCH_URLS (25)
is 400 or 413 and inserts nothing. A list of exactly 25 PDF URLs from an
accepted token is 201 and inserts 25 jobs.

A client may have at most MAX_OPEN_BATCHES (2) batches in status ``running``
or ``discovering``. The next create is 429 or 409 and inserts nothing.
The cap is per token: another token can still create a batch. Concurrent
creates must not push that client over the cap.

Discovery mode (``{"url": ..., "max_forms": N}``) is subject to the same
authentication rule. An accepted token cannot enqueue more than
MAX_BATCH_URLS jobs this way either: the response is 400 or 413 with nothing
inserted, or 201 with at most MAX_BATCH_URLS jobs.

Not covered here
----------------
TXT and CSV uploads do not call the LLM. ``llm_discover`` is not reached by
POST /api/batch/create because that handler always uses strategy ``auto``,
which sets ``llm_timeout`` to 0. ``title_llm.make_title`` is imported by the
worker module and is not called from ``process_job``. The deploy scripts'
``--max-instances 10`` multiplier is outside this process.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest
import requests
from werkzeug.serving import make_server

from pypdf import PdfReader

from app.services import llm_router
from app.services.storage import get_db

MARKER = "FORMGENOME_ATTACK_MARKER_9f3a"
MAX_GEMINI_CALLS_PER_CLIENT = 6
MAX_BATCH_URLS = 25
MAX_OPEN_BATCHES = 2
TOKEN_A = "client-a-token"
TOKEN_B = "client-b-token"
IP_A = "203.0.113.10"
IP_A_OTHER = "203.0.113.11"
IP_B = "198.51.100.20"

GEMINI_CALLS = []
GEMINI_LOCK = threading.Lock()


def _pdf_bytes() -> bytes:
    def esc(value: str) -> str:
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    page_one = (MARKER + " application signature required many fields ") * 4
    page_two = "Second page signature lines and fields " * 4
    streams = [
        f"BT /F1 12 Tf 72 700 Td ({esc(page_one)}) Tj ET".encode(),
        f"BT /F1 12 Tf 72 700 Td ({esc(page_two)}) Tj ET".encode(),
    ]
    objs = {
        1: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        2: f"<< /Length {len(streams[0])} >>stream\n".encode() + streams[0] + b"\nendstream",
        3: f"<< /Length {len(streams[1])} >>stream\n".encode() + streams[1] + b"\nendstream",
        4: b"<< /Type /Page /Parent 6 0 R /MediaBox [0 0 612 792] /Contents 2 0 R /Resources << /Font << /F1 1 0 R >> >> >>",
        5: b"<< /Type /Page /Parent 6 0 R /MediaBox [0 0 612 792] /Contents 3 0 R /Resources << /Font << /F1 1 0 R >> >> >>",
        6: b"<< /Type /Pages /Kids [4 0 R 5 0 R] /Count 2 >>",
        7: b"<< /Type /Catalog /Pages 6 0 R >>",
    }
    out = b"%PDF-1.4\n"
    xref_pos = []
    for number in range(1, 8):
        xref_pos.append(len(out))
        out += f"{number} 0 obj\n".encode() + objs[number] + b"\nendobj\n"
    xref_start = len(out)
    out += b"xref\n0 8\n0000000000 65535 f \n"
    for pos in xref_pos:
        out += f"{pos:010d} 00000 n \n".encode()
    out += f"trailer<< /Size 8 /Root 7 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode()
    return out


PDF_BYTES = _pdf_bytes()


class PdfHandler(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        body = PDF_BYTES
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def _install_gemini_stub():
    """Replace the SDK's socket send. Every earlier step still runs.

    google-genai 3.6.1 (the version pip resolves from requirements.txt's
    ``google-genai>=0.2.0``) builds the generateContent request in
    Models._generate_content and posts it from BaseApiClient._request_once
    via httpx. This stand-in records that request and returns a JSON body
    the same parser turns into response.text.
    """
    from google.genai import _api_client

    def fake_request_once(self, http_request, stream=False):
        data = http_request.data
        if not isinstance(data, dict):
            data = json.loads(data)
        else:
            data = json.loads(json.dumps(data))
        parts = []
        for content in data.get("contents") or []:
            for part in content.get("parts") or []:
                if isinstance(part, dict) and "text" in part:
                    parts.append(part["text"])
        generation = data.get("generationConfig") or {}
        record = {
            "method": http_request.method,
            "url": str(http_request.url),
            "max_output_tokens": generation.get("maxOutputTokens"),
            "prompt": "\n".join(parts),
        }
        with GEMINI_LOCK:
            GEMINI_CALLS.append(record)
        body = json.dumps({
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [{
                        "text": json.dumps({
                            "complexity_score": 80,
                            "signature_required": True,
                            "total_field_count": 40,
                            "title": "Attacker Form",
                            "entity": "Example Credit Union",
                        })
                    }],
                },
                "finishReason": "STOP",
            }]
        })
        return _api_client.HttpResponse({"Content-Type": "application/json"}, [body])

    _api_client.BaseApiClient._request_once = fake_request_once


_install_gemini_stub()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _count(sql):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        return cursor.fetchone()["n"]


def job_count():
    return _count("SELECT COUNT(*) AS n FROM job_queue")


def open_batch_count():
    return _count(
        "SELECT COUNT(*) AS n FROM batch_jobs "
        "WHERE status IN ('running', 'discovering')"
    )


def _truncate():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("TRUNCATE job_queue, batch_jobs, forms RESTART IDENTITY")


def _reset_limiter():
    llm_router._gemini_rate_limiter = None
    llm_router._anthropic_rate_limiter = None


@pytest.fixture(scope="session")
def pdf_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), PdfHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


@pytest.fixture(scope="session")
def app():
    from main import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture(scope="session")
def live(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_state(app):
    GEMINI_CALLS.clear()
    PdfHandler.hits = 0
    _reset_limiter()
    _truncate()
    yield
    GEMINI_CALLS.clear()


def pdf_url(pdf_server):
    host, port = pdf_server.server_address
    return f"http://{host}:{port}/attacker-form.pdf"


def post_analyze(client, pdf_server, token=None, remote_addr=IP_A, as_file=False):
    headers = {}
    if token:
        headers.update(_auth(token))
    if as_file:
        return client.post(
            "/api/analyze/upload",
            data={"file": (BytesIO(PDF_BYTES), "attacker-form.pdf")},
            headers=headers,
            content_type="multipart/form-data",
            environ_base={"REMOTE_ADDR": remote_addr},
        )
    return client.post(
        "/api/analyze/upload",
        json={"url": pdf_url(pdf_server)},
        headers=headers,
        environ_base={"REMOTE_ADDR": remote_addr},
    )


def post_batch(client, payload, token=None, remote_addr=IP_A):
    headers = {}
    if token:
        headers.update(_auth(token))
    return client.post(
        "/api/batch/create",
        json=payload,
        headers=headers,
        environ_base={"REMOTE_ADDR": remote_addr},
    )


def _evidence():
    with GEMINI_LOCK:
        calls = list(GEMINI_CALLS)
    return {
        "downloads": PdfHandler.hits,
        "gemini_calls": len(calls),
        "urls": [call["url"] for call in calls],
        "max_output_tokens": [call["max_output_tokens"] for call in calls],
        "marker_in_prompt": [MARKER in call["prompt"] for call in calls],
        "jobs": job_count(),
        "open_batches": open_batch_count(),
    }


def test_pdf_fixture_has_extractable_marker():
    reader = PdfReader(BytesIO(PDF_BYTES))
    assert len(reader.pages) == 2
    assert MARKER in (reader.pages[0].extract_text() or "")


def test_authorized_url_analysis_reaches_gemini_with_pdf_text(client, pdf_server):
    """A caller the server has accepted still gets the two-stage Gemini analysis."""
    response = post_analyze(client, pdf_server, token=TOKEN_A)
    evidence = _evidence()
    assert response.status_code == 200, evidence
    body = response.get_json()
    assert body["success"] is True
    assert evidence["gemini_calls"] == 2
    assert evidence["marker_in_prompt"] == [True, True]
    assert evidence["max_output_tokens"] == [600, 4000]
    assert all(url.endswith(":generateContent") for url in evidence["urls"])
    assert "generativelanguage.googleapis.com" in evidence["urls"][0]


def test_authorized_file_upload_reaches_gemini(client, pdf_server):
    response = post_analyze(client, pdf_server, token=TOKEN_A, as_file=True)
    evidence = _evidence()
    assert response.status_code == 200, evidence
    assert evidence["gemini_calls"] == 2
    assert evidence["marker_in_prompt"] == [True, True]
    assert evidence["max_output_tokens"] == [600, 4000]


def test_anonymous_url_analysis_is_refused_before_download_or_gemini(client, pdf_server):
    response = post_analyze(client, pdf_server, token=None)
    evidence = _evidence()
    assert response.status_code in (401, 403) and evidence["downloads"] == 0 and evidence["gemini_calls"] == 0, (
        f"status={response.status_code} downloads={evidence['downloads']} "
        f"calls={evidence['gemini_calls']} tokens={evidence['max_output_tokens']} "
        f"marker={evidence['marker_in_prompt']} urls={evidence['urls']}"
    )


def test_unknown_token_url_analysis_is_refused_before_download_or_gemini(client, pdf_server):
    response = post_analyze(client, pdf_server, token="not-a-registered-token")
    evidence = _evidence()
    assert response.status_code in (401, 403), evidence
    assert evidence["downloads"] == 0, evidence
    assert evidence["gemini_calls"] == 0, evidence


def test_anonymous_file_upload_is_refused_before_tempfile_or_gemini(client, pdf_server, monkeypatch):
    created = []
    import tempfile
    real_named = tempfile.NamedTemporaryFile

    def counting_named(*args, **kwargs):
        created.append(kwargs.get("suffix"))
        return real_named(*args, **kwargs)

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", counting_named)
    response = post_analyze(client, pdf_server, token=None, as_file=True)
    evidence = _evidence()
    assert response.status_code in (401, 403) and created == [] and evidence["gemini_calls"] == 0, (
        f"status={response.status_code} temps={created} calls={evidence['gemini_calls']} "
        f"tokens={evidence['max_output_tokens']} marker={evidence['marker_in_prompt']}"
    )


def test_one_client_cannot_exhaust_gemini_budget_of_another(client, pdf_server):
    statuses = []
    for _ in range(4):
        response = post_analyze(client, pdf_server, token=TOKEN_A, remote_addr=IP_A)
        statuses.append(response.status_code)
    with GEMINI_LOCK:
        calls_for_a = len(GEMINI_CALLS)
    extra = post_analyze(client, pdf_server, token=TOKEN_A, remote_addr=IP_A_OTHER)
    with GEMINI_LOCK:
        calls_after_same_token = len(GEMINI_CALLS)
    other = post_analyze(client, pdf_server, token=TOKEN_B, remote_addr=IP_A)
    with GEMINI_LOCK:
        calls_after_b = len(GEMINI_CALLS)
    evidence = _evidence()
    evidence["statuses_a"] = statuses
    evidence["same_token_other_ip"] = extra.status_code
    evidence["other_client"] = other.status_code
    assert statuses[0] == 200, evidence
    assert calls_for_a <= MAX_GEMINI_CALLS_PER_CLIENT, evidence
    assert 429 in statuses[1:], evidence
    assert extra.status_code == 429, evidence
    assert calls_after_same_token == calls_for_a, evidence
    assert other.status_code == 200, evidence
    assert calls_after_b > calls_after_same_token, evidence


def test_anonymous_batch_is_not_queued_and_worker_does_not_call_gemini(client, pdf_server):
    before_calls = len(GEMINI_CALLS)
    response = post_batch(client, {"urls": [pdf_url(pdf_server)], "name": "anon"})
    inserted = job_count()
    if response.status_code == 201 and inserted:
        from app.services.batch_processor import BatchConfig, process_job
        from app.services.storage import get_next_job
        job = get_next_job("worker_test")
        assert job is not None
        process_job(job, BatchConfig())
    evidence = _evidence()
    assert response.status_code in (401, 403), evidence
    assert inserted == 0, evidence
    assert len(GEMINI_CALLS) == before_calls, evidence


def test_anonymous_thousand_url_batch_inserts_nothing(client):
    urls = [f"http://127.0.0.1/attacker-{i}.pdf" for i in range(1000)]
    response = post_batch(client, {"urls": urls, "name": "flood"})
    evidence = _evidence()
    assert response.status_code in (401, 403), evidence
    assert evidence["jobs"] == 0, evidence
    assert evidence["open_batches"] == 0, evidence


def test_authorized_batch_over_cap_inserts_nothing(client):
    urls = [f"http://127.0.0.1/capped-{i}.pdf" for i in range(1000)]
    response = post_batch(client, {"urls": urls, "name": "over"}, token=TOKEN_A)
    evidence = _evidence()
    assert response.status_code in (400, 413), evidence
    assert evidence["jobs"] == 0, evidence
    assert evidence["open_batches"] == 0, evidence


def test_authorized_batch_at_cap_is_accepted(client):
    urls = [f"http://127.0.0.1/ok-{i}.pdf" for i in range(MAX_BATCH_URLS)]
    response = post_batch(client, {"urls": urls, "name": "exact"}, token=TOKEN_A)
    evidence = _evidence()
    assert response.status_code == 201, evidence
    assert evidence["jobs"] == MAX_BATCH_URLS, evidence
    assert evidence["open_batches"] == 1, evidence


def test_open_batch_cap_is_per_client(client):
    statuses = []
    for index in range(MAX_OPEN_BATCHES + 1):
        response = post_batch(
            client,
            {"urls": [f"http://127.0.0.1/open-a-{index}.pdf"], "name": f"a{index}"},
            token=TOKEN_A,
        )
        statuses.append(response.status_code)
    other = post_batch(
        client,
        {"urls": ["http://127.0.0.1/open-b-0.pdf"], "name": "b0"},
        token=TOKEN_B,
    )
    evidence = _evidence()
    evidence["statuses"] = statuses
    evidence["other"] = other.status_code
    assert statuses[:MAX_OPEN_BATCHES] == [201, 201], evidence
    assert statuses[-1] in (429, 409), evidence
    assert other.status_code == 201, evidence
    # Two open batches for A plus one for B. A's third create inserted nothing.
    assert evidence["open_batches"] == MAX_OPEN_BATCHES + 1, evidence
    assert evidence["jobs"] == MAX_OPEN_BATCHES + 1, evidence


def test_open_batch_cap_holds_under_concurrent_creates(live):
    base = f"http://127.0.0.1:{live.server_port}"

    def create(index):
        response = requests.post(
            base + "/api/batch/create",
            json={"urls": [f"http://127.0.0.1/race-{index}.pdf"], "name": f"race{index}"},
            headers=_auth(TOKEN_A),
            timeout=30,
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=5) as pool:
        statuses = list(pool.map(create, range(5)))
    evidence = _evidence()
    evidence["statuses"] = statuses
    assert open_batch_count() <= MAX_OPEN_BATCHES, evidence
    assert job_count() <= MAX_OPEN_BATCHES, evidence
    assert statuses.count(201) <= MAX_OPEN_BATCHES, evidence


def test_anonymous_discovery_does_not_run_or_queue(client, monkeypatch):
    called = []

    def tripwire(url, max_forms=200, strategy="auto"):
        called.append((url, max_forms, strategy))
        return [f"http://127.0.0.1/discovered-{i}.pdf" for i in range(max_forms)]

    monkeypatch.setattr("app.api.batch.discover_forms_smart", tripwire)
    response = post_batch(
        client,
        {"url": "http://127.0.0.1/seed", "max_forms": 5000, "name": "discover"},
    )
    evidence = _evidence()
    evidence["discovery_calls"] = called
    assert response.status_code in (401, 403), evidence
    assert called == [], evidence
    assert evidence["jobs"] == 0, evidence
    assert evidence["open_batches"] == 0, evidence


def test_authorized_discovery_cannot_enqueue_more_than_the_batch_cap(client, monkeypatch):
    def discover(url, max_forms=200, strategy="auto"):
        return [f"http://127.0.0.1/discovered-{i}.pdf" for i in range(max_forms)]

    monkeypatch.setattr("app.api.batch.discover_forms_smart", discover)
    response = post_batch(
        client,
        {"url": "http://127.0.0.1/seed", "max_forms": 1000, "name": "discover"},
        token=TOKEN_A,
    )
    evidence = _evidence()
    if response.status_code == 201:
        assert evidence["jobs"] <= MAX_BATCH_URLS, evidence
        assert evidence["open_batches"] == 1, evidence
    else:
        assert response.status_code in (400, 413), evidence
        assert evidence["jobs"] == 0, evidence
        assert evidence["open_batches"] == 0, evidence
