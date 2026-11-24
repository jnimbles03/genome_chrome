"""SSRF regression tests for server-side URL fetches.

These tests describe the safe behavior. They fail while the service still
connects to an attacker-supplied internal address, and they pass once every
server-side fetch refuses those destinations before opening a socket.

Run from the repository root, with the packages pinned in
form-genome-structured-cloudrun/requirements.txt (Flask, requests==2.31.0,
urllib3==2.1.0, beautifulsoup4, pypdf, psycopg2, playwright) and a local
PostgreSQL database named formgenome that accepts the default DB_USER with
an empty DB_PASSWORD:

    DB_HOST=127.0.0.1 DB_NAME=formgenome DB_USER=postgres \\
        pytest form-genome-structured-cloudrun/tests/test_ssrf_unvalidated_fetch.py

The fixture assigns documentation and private addresses to the loopback
interface so connection attempts stay on this machine. That needs permission
to run `ip addr add`.
"""

import json
import logging
import os
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ["DB_HOST"] = "127.0.0.1"
os.environ["DB_NAME"] = os.environ.get("DB_NAME", "formgenome")
os.environ["DB_USER"] = os.environ.get("DB_USER", "postgres")
os.environ["DB_PASSWORD"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["GOOGLE_API_KEY"] = ""
os.environ["GOOGLE_CSE_ID"] = ""

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SECRET = "INTERNAL_SECRET_MARKER"
PUBLIC_TEXT = "PUBLIC_FORM_OK"

# Addresses a correct filter must reject. Assigned to lo by the session fixture
# so a connection is proof the client tried that destination, and so the test
# never sends packets onto a real network.
PRIVATE_HOSTS = (
    "10.1.2.3",
    "172.16.9.9",
    "192.168.50.9",
    "169.254.169.254",
)
PUBLIC_HOST = "93.184.216.34"

logging.disable(logging.CRITICAL)


def _pdf(text: str) -> bytes:
    content = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET\n".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


SECRET_PDF = _pdf(SECRET)
PUBLIC_PDF = _pdf(PUBLIC_TEXT)


class _State:
    def __init__(self, mode, body=b"", status=200, content_type="application/pdf", location=""):
        self.mode = mode
        self.body = body
        self.status = status
        self.content_type = content_type
        self.location = location
        self.hits = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        state = self.server.state
        state.hits.append(
            {
                "path": self.path,
                "host": self.headers.get("Host"),
                "headers": {key: value for key, value in self.headers.items()},
            }
        )
        if state.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", state.location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = state.body
        self.send_response(state.status)
        self.send_header("Content-Type", state.content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host, state, port=0):
        self.state = state
        super().__init__((host, port), _Handler)


class _V6Server(_Server):
    address_family = socket.AF_INET6


@contextmanager
def served(host, mode, body=b"", status=200, content_type="application/pdf", location="", ipv6=False):
    state = _State(mode, body, status, content_type, location)
    server = (_V6Server if ipv6 else _Server)(host, state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _url(server, path):
    host, port = server.server_address[0], server.server_address[1]
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}{path}"


def _ensure_local_ips():
    for ip in (*PRIVATE_HOSTS, PUBLIC_HOST):
        subprocess.run(
            ["sudo", "ip", "addr", "add", f"{ip}/32", "dev", "lo"],
            capture_output=True,
            text=True,
        )
        probe = socket.socket()
        try:
            probe.bind((ip, 0))
        except OSError as exc:
            pytest.fail(f"could not bind {ip} on this machine: {exc}")
        finally:
            probe.close()


@pytest.fixture(scope="session", autouse=True)
def local_destination_addresses():
    _ensure_local_ips()


@pytest.fixture(scope="session")
def app():
    from main import create_app

    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def _clear_queue():
    from app.services.storage import get_db

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM job_queue")


def _process_queued(url_part):
    """Run the worker's claim-and-process steps for every queued copy of url_part."""
    from app.services.batch_processor import BatchConfig, process_job
    from app.services.storage import get_db, get_next_job

    config = BatchConfig(rate_limit_enabled=False, pdf_timeout=5, pdf_max_size=1024 * 1024)
    deferred = []
    handled = 0
    for _ in range(20):
        job = get_next_job("ssrf-test")
        if not job:
            break
        if url_part not in (job.get("url") or ""):
            deferred.append(job["job_id"])
            continue
        handled += 1
        try:
            process_job(job, config)
        except Exception:
            pass
    if deferred:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE job_queue SET status = 'pending', worker_id = NULL WHERE job_id = ANY(%s)",
                (deferred,),
            )
    return handled


def _invoke_analyze(url):
    from app.api.analyze import analyze_url_logic

    try:
        result = analyze_url_logic(url)
    except Exception as exc:
        return f"raised:{exc}"
    return json.dumps(result)


def _invoke_download(url):
    from app.services.batch_processor import download_pdf

    try:
        payload = download_pdf(url, timeout=5, max_size=1024 * 1024)
    except Exception as exc:
        return f"raised:{exc}"
    return payload.decode("latin-1", errors="replace")


def _assert_silent(server, detail):
    assert server.state.hits == [], (
        f"server at {server.server_address} received "
        f"{[hit['path'] for hit in server.state.hits]} "
        f"headers={server.state.hits[0]['headers'] if server.state.hits else {}} "
        f"detail={detail[:1500]}"
    )
    assert SECRET not in detail


@contextmanager
def _dns(host, answers):
    """Replace socket.getaddrinfo for one hostname.

    urllib3.util.connection.create_connection (urllib3 2.1.0) calls
    socket.getaddrinfo(host, port, family, socket.SOCK_STREAM) and connects to
    every result until one succeeds. `answers` is a function of the 1-based
    call count and returns IP strings for that lookup.
    """
    real = socket.getaddrinfo
    calls = {"n": 0}

    def wrapped(lookup_host, port, *args, **kwargs):
        if lookup_host == host:
            calls["n"] += 1
            records = []
            for ip in answers(calls["n"]):
                if ":" in ip:
                    records.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0)))
                else:
                    records.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)))
            return records
        return real(lookup_host, port, *args, **kwargs)

    socket.getaddrinfo = wrapped
    try:
        yield calls
    finally:
        socket.getaddrinfo = real


def test_public_routable_pdf_is_still_analyzed():
    """A globally routable http PDF is still fetched and its text is returned."""
    with served(PUBLIC_HOST, "pdf", PUBLIC_PDF) as public:
        detail = _invoke_analyze(_url(public, "/form.pdf"))
    assert public.state.hits, "expected the allowed public URL to be fetched"
    assert PUBLIC_TEXT in detail
    assert SECRET not in detail


def test_analyze_endpoint_does_not_fetch_loopback_pdf(client):
    with served("127.0.0.1", "pdf", SECRET_PDF) as secret:
        response = client.post("/api/analyze/upload", json={"url": _url(secret, "/secret.pdf")})
        detail = response.get_data(as_text=True)
        _assert_silent(secret, detail)
    assert response.status_code >= 400


def test_analyze_endpoint_does_not_reflect_internal_status_or_body(client):
    with served(
        "127.0.0.1",
        "body",
        body=f"status-body {SECRET}".encode(),
        status=503,
        content_type="text/plain",
    ) as secret:
        response = client.post("/api/analyze/upload", json={"url": _url(secret, "/health")})
        detail = response.get_data(as_text=True)
        _assert_silent(secret, detail)
    assert "503" not in detail
    assert "text/plain" not in detail


def test_analyze_endpoint_does_not_follow_redirect_to_link_local(client):
    with served("169.254.169.254", "pdf", SECRET_PDF) as secret:
        with served(PUBLIC_HOST, "redirect", location=_url(secret, "/secret.pdf")) as public:
            response = client.post(
                "/api/analyze/upload",
                json={"url": _url(public, "/redirect.pdf")},
            )
            detail = response.get_data(as_text=True)
            _assert_silent(secret, detail)
    assert response.status_code >= 400


@pytest.mark.parametrize(
    "html",
    [
        "<html><a href='{target}'>form</a></html>",
        "<html><iframe src='{target}'></iframe></html>",
        "<html><script>var u = \"{target}\";</script></html>",
    ],
)
def test_deep_peek_does_not_fetch_internal_pdf_link(html):
    with served("10.1.2.3", "pdf", SECRET_PDF) as secret:
        page = html.format(target=_url(secret, "/secret.pdf")).encode()
        with served(PUBLIC_HOST, "body", page, content_type="text/html") as public:
            detail = _invoke_analyze(_url(public, "/landing"))
            _assert_silent(secret, detail)


@pytest.mark.parametrize(
    "host,path,ipv6",
    [
        ("127.0.0.1", "/secret.pdf", False),
        ("127.0.0.2", "/secret.pdf", False),
        ("0.0.0.0", "/secret.pdf", False),
        ("::1", "/secret.pdf", True),
    ],
)
@pytest.mark.parametrize("sink", ["analyze", "download"])
def test_literal_local_addresses_are_not_fetched(sink, host, path, ipv6):
    with served(host, "pdf", SECRET_PDF, ipv6=ipv6) as secret:
        url = _url(secret, path)
        detail = _invoke_analyze(url) if sink == "analyze" else _invoke_download(url)
        _assert_silent(secret, detail)


@pytest.mark.parametrize(
    "spelling",
    [
        "2130706433",
        "0x7f000001",
        "0177.0.0.1",
        "127.1",
        "localhost",
        "[::ffff:127.0.0.1]",
    ],
)
@pytest.mark.parametrize("sink", ["analyze", "download"])
def test_loopback_spellings_are_not_fetched(sink, spelling):
    with served("127.0.0.1", "pdf", SECRET_PDF) as secret:
        port = secret.server_address[1]
        url = f"http://{spelling}:{port}/secret.pdf"
        detail = _invoke_analyze(url) if sink == "analyze" else _invoke_download(url)
        _assert_silent(secret, detail)


@pytest.mark.parametrize("host", PRIVATE_HOSTS)
@pytest.mark.parametrize("sink", ["analyze", "download"])
def test_private_and_link_local_literals_are_not_fetched(sink, host):
    with served(host, "pdf", SECRET_PDF) as secret:
        url = _url(secret, "/secret.pdf")
        detail = _invoke_analyze(url) if sink == "analyze" else _invoke_download(url)
        _assert_silent(secret, detail)


@pytest.mark.parametrize("sink", ["analyze", "download"])
def test_userinfo_cannot_hide_loopback_host(sink):
    with served("127.0.0.1", "pdf", SECRET_PDF) as secret:
        port = secret.server_address[1]
        url = f"http://{PUBLIC_HOST}@{secret.server_address[0]}:{port}/secret.pdf"
        detail = _invoke_analyze(url) if sink == "analyze" else _invoke_download(url)
        _assert_silent(secret, detail)


@pytest.mark.parametrize("sink", ["analyze", "download"])
def test_every_address_from_one_lookup_is_refused(sink):
    """One getaddrinfo answer lists a closed public address and then loopback.

    urllib3 connects to each result until one accepts. Checking only the first
    address, then calling requests.get on the hostname, still reaches loopback.
    """
    with served("127.0.0.1", "pdf", SECRET_PDF) as secret:
        port = secret.server_address[1]

        def answers(_call):
            return [PUBLIC_HOST, "127.0.0.1"]

        with _dns("multi.ssrf.test", answers):
            url = f"http://multi.ssrf.test:{port}/secret.pdf"
            detail = _invoke_analyze(url) if sink == "analyze" else _invoke_download(url)
        _assert_silent(secret, detail)


@contextmanager
def _same_port(public_state, secret_state):
    public = _Server(PUBLIC_HOST, public_state, 0)
    secret = _Server("127.0.0.1", secret_state, public.server_address[1])
    threads = []
    try:
        for server in (public, secret):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            threads.append(thread)
        yield public, secret
    finally:
        for server in (public, secret):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)


@pytest.mark.parametrize("sink", ["analyze", "download"])
def test_later_lookup_cannot_rebind_to_loopback(sink):
    """First lookup is a listening public address; every later lookup is loopback.

    A check that resolves the name, allows that public address, then calls
    requests.get on the original hostname looks the name up again and connects
    to loopback. Pinning the socket to the address that was allowed does not.
    """
    public_state = _State("pdf", PUBLIC_PDF)
    secret_state = _State("pdf", SECRET_PDF)
    with _same_port(public_state, secret_state) as (public, secret):
        port = public.server_address[1]

        def answers(call):
            if call == 1:
                return [PUBLIC_HOST]
            return ["127.0.0.1"]

        with _dns("rebind.ssrf.test", answers):
            url = f"http://rebind.ssrf.test:{port}/secret.pdf"
            detail = _invoke_analyze(url) if sink == "analyze" else _invoke_download(url)
        _assert_silent(secret, detail)


@pytest.mark.parametrize("sink", ["analyze", "download"])
def test_redirect_from_public_host_does_not_reach_link_local(sink):
    with served("169.254.169.254", "pdf", SECRET_PDF) as secret:
        with served(PUBLIC_HOST, "redirect", location=_url(secret, "/latest/secret.pdf")) as public:
            url = _url(public, "/form.pdf")
            detail = _invoke_analyze(url) if sink == "analyze" else _invoke_download(url)
            _assert_silent(secret, detail)


def test_batch_create_worker_does_not_fetch_private_pdf(client):
    _clear_queue()
    with served("10.1.2.3", "pdf", SECRET_PDF) as secret:
        url = _url(secret, "/secret.pdf")
        response = client.post("/api/batch/create", json={"urls": [url], "name": "ssrf"})
        _process_queued(url)
        _assert_silent(secret, response.get_data(as_text=True))


def test_batch_worker_does_not_follow_redirect_to_loopback(client):
    _clear_queue()
    with served("127.0.0.1", "pdf", SECRET_PDF) as secret:
        with served(PUBLIC_HOST, "redirect", location=_url(secret, "/secret.pdf")) as public:
            url = _url(public, "/form.pdf")
            response = client.post("/api/batch/create", json={"urls": [url]})
            _process_queued(url)
            _assert_silent(secret, response.get_data(as_text=True))


def test_team_reanalyze_worker_does_not_fetch_stored_url(client):
    _clear_queue()
    with served("192.168.50.9", "pdf", SECRET_PDF) as secret:
        url = _url(secret, "/internal")
        synced = client.post(
            "/api/team/sync",
            json={
                "user_id": "attacker",
                "form_data": {
                    "source_url": url,
                    "form_name": "Internal",
                    "entity_name": "Lab",
                    "complexity_score": 1,
                },
            },
        )
        listed = client.get("/api/team/forms")
        forms = listed.get_json(silent=True) or {}
        match = [row for row in forms.get("forms", []) if row.get("source_url") == url]
        assert match, f"sync did not store the attacker URL: {synced.get_data(as_text=True)} {listed.get_data(as_text=True)}"
        client.post("/api/team/reanalyze", json={"analysis_ids": [match[0]["analysis_id"]]})
        _process_queued(url)
        _assert_silent(secret, listed.get_data(as_text=True))


def test_batch_discovery_does_not_fetch_internal_seed(client):
    with served("127.0.0.1", "pdf", SECRET_PDF) as secret:
        response = client.post("/api/batch/create", json={"url": _url(secret, "/"), "max_forms": 5})
        detail = response.get_data(as_text=True)
        if secret.state.hits == [] and "playwright" in detail.lower() and "no module" in detail.lower():
            pytest.fail(
                "discovery never imported playwright==1.40.0, so this test did not reach the fetch"
            )
        _assert_silent(secret, detail)


def test_file_url_is_not_read(client, tmp_path):
    secret_file = tmp_path / "secret.pdf"
    secret_file.write_bytes(SECRET.encode())
    response = client.post("/api/analyze/upload", json={"url": secret_file.as_uri()})
    detail = response.get_data(as_text=True)
    assert SECRET not in detail
    assert response.status_code >= 400
