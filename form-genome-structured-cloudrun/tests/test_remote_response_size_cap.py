"""Remote response bodies must be capped before they are buffered.

These tests fail while PDF analysis and site discovery read an entire
remote response into memory. They pass once every public fetch stops at
the same limits the product already uses for uploads and for the batch
worker: 5_000_000 bytes for HTML, robots, and sitemaps, and 50 MiB for
PDFs. A response may exceed the cap by at most one small read chunk.

The cap has to be enforced on the bytes taken from the socket. Rejecting
only when a Content-Length header is present is not enough: a body with
no Content-Length, or a header that understates nothing because it is
absent, must still stop at the cap. A header that is already over the
cap must not be followed by a full read.

Run from form-genome-structured-cloudrun:

    python3 -m pytest tests/test_remote_response_size_cap.py -q

Paths not covered here, and why:
- enhanced_crawler.EnhancedWebCrawler._harvest_via_playwright fetches and
  calls resp.body() only after Chromium launches. This environment has
  the Playwright package but not the browser binary, so that branch
  returns before those reads. The Dockerfile does install the browser.
- llm_discover.fetch reads r.text with no cap, but POST /api/batch/create
  always calls discover_forms_smart(..., strategy="auto"), and every
  auto strategy sets llm_timeout to 0, so that fetch is not reached.
"""

from __future__ import annotations

import os
import socket
import socketserver
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.pop("GOOGLE_API_KEY", None)
os.environ.pop("GOOGLE_CSE_ID", None)
os.environ.pop("DB_HOST", None)

from app.api.analyze import MAX_FILE_SIZE  # noqa: E402
from app.services.batch_processor import BatchConfig  # noqa: E402
from app.services.enhanced_crawler import DEFAULT_CFG  # noqa: E402
from main import create_app  # noqa: E402

# Limits already declared for the capped paths. Remote fetches have to
# honor these even when the caller points them at a host they control.
HTML_CAP = 5_000_000
PDF_CAP = 50 * 1024 * 1024
OVERSHOOT = 1_000_000
# Kernel send buffer on this host bottoms out at 4608 bytes, and requests
# 2.31.0 reads Response.content in 10 KiB chunks. Anything past the cap by
# more than this was not stopped at the cap.
SLACK = 64 * 1024
CHUNK = 4096


@dataclass
class Route:
    content_type: str
    size: int
    prefix: bytes = b""
    # "chunked" omits Content-Length. "length" sends Content-Length: size.
    mode: str = "chunked"

    def __post_init__(self) -> None:
        if len(self.prefix) > self.size:
            raise ValueError("prefix is larger than the declared body")


@dataclass
class Hit:
    method: str
    path: str
    body_bytes: int


class TrackingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, routes: dict):
        super().__init__(("127.0.0.1", 0), TrackingHandler)
        self.routes = routes
        self.hits = []
        self._lock = threading.Lock()
        self._idle = threading.Condition()
        self._inflight = 0

    def note_start(self) -> None:
        with self._idle:
            self._inflight += 1

    def note_end(self) -> None:
        with self._idle:
            self._inflight -= 1
            self._idle.notify_all()

    def add_hit(self, hit: Hit) -> None:
        with self._lock:
            self.hits.append(hit)

    def snapshot(self) -> list:
        with self._lock:
            return list(self.hits)

    def wait_until_idle(self, timeout: float = 30) -> None:
        with self._idle:
            while self._inflight > 0:
                if not self._idle.wait(timeout):
                    return


class TrackingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2048)
        self.connection.settimeout(20)

    def handle(self) -> None:
        self.server.note_start()
        try:
            super().handle()
        finally:
            self.server.note_end()

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_HEAD(self) -> None:
        self._respond(include_body=False)

    def do_GET(self) -> None:
        self._respond(include_body=True)

    def _respond(self, include_body: bool) -> None:
        path = self.path.split("?", 1)[0]
        route = self.server.routes.get(path)
        sent = 0
        try:
            if route is None:
                body = b"not found"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if include_body:
                    self.wfile.write(body)
                    self.wfile.flush()
                return

            self.send_response(200)
            self.send_header("Content-Type", route.content_type)
            self.send_header("Connection", "close")
            if route.mode == "length":
                self.send_header("Content-Length", str(route.size))
            else:
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if not include_body:
                return

            for piece in _pieces(route):
                if route.mode == "chunked":
                    self.wfile.write(f"{len(piece):x}\r\n".encode("ascii"))
                    self.wfile.write(piece)
                    self.wfile.write(b"\r\n")
                else:
                    self.wfile.write(piece)
                self.wfile.flush()
                sent += len(piece)
            if route.mode == "chunked":
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            if include_body:
                self.server.add_hit(Hit(self.command, path, sent))
        self.close_connection = True


def _pieces(route: Route):
    remaining = route.size
    if route.prefix:
        yield route.prefix
        remaining -= len(route.prefix)
    block = b"\0" * CHUNK
    while remaining > 0:
        take = block if remaining >= CHUNK else block[:remaining]
        yield take
        remaining -= len(take)


class Started:
    def __init__(self, server: TrackingServer, thread: threading.Thread):
        self.server = server
        self.thread = thread

    @property
    def base(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def hits(self) -> list:
        self.server.wait_until_idle()
        return self.server.snapshot()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def serve(routes: dict) -> Started:
    server = TrackingServer(routes)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    started = Started(server, thread)
    return started


def _gets(hits: list, path: str) -> list:
    return [hit for hit in hits if hit.method == "GET" and hit.path == path]


def assert_capped(hits: list, path: str, cap: int) -> None:
    gets = _gets(hits, path)
    assert gets, f"{path} was never fetched; hits={hits}"
    for hit in gets:
        assert hit.body_bytes <= cap + SLACK, (
            f"{path} delivered {hit.body_bytes} body bytes; "
            f"the read has to stop by {cap} (slack {SLACK})"
        )


def assert_fully_read(hits: list, path: str, size: int) -> None:
    gets = _gets(hits, path)
    assert gets, f"{path} was never fetched; hits={hits}"
    assert gets[0].body_bytes == size, (
        f"{path} delivered {gets[0].body_bytes} of {size} bytes; "
        f"a body under the cap still has to be downloaded"
    )


def assert_not_forbidden(resp) -> None:
    assert resp.status_code not in (401, 403), resp.get_data(as_text=True)


def assert_no_oversized_filesize(resp, cap: int) -> None:
    payload = resp.get_json(silent=True) or {}
    results = payload.get("results") if isinstance(payload, dict) else None
    if isinstance(results, dict) and "fileSize" in results:
        assert results["fileSize"] <= cap + SLACK, results["fileSize"]


def html_page(pdf_href: str | None) -> bytes:
    link = f'<a href="{pdf_href}">form</a>' if pdf_href else "<p>no forms</p>"
    return f"<!DOCTYPE html><html><body>{link}</body></html>".encode("utf-8")


def test_declared_limits_match_the_caps_these_tests_enforce():
    """A fix that only raises the declared limits does not satisfy the tests."""
    assert DEFAULT_CFG["max_content"] <= HTML_CAP
    assert MAX_FILE_SIZE <= PDF_CAP
    assert BatchConfig.pdf_max_size <= PDF_CAP


@pytest.mark.parametrize("mode", ["chunked", "length"])
def test_analyze_upload_direct_pdf_stops_at_pdf_cap(client, mode):
    size = PDF_CAP + OVERSHOOT
    started = serve({
        "/form.pdf": Route("application/pdf", size, mode=mode),
    })
    try:
        resp = client.post(
            "/api/analyze/upload",
            json={"url": f"{started.base}/form.pdf"},
        )
        assert_not_forbidden(resp)
        assert_capped(started.hits(), "/form.pdf", PDF_CAP)
        assert_no_oversized_filesize(resp, PDF_CAP)
    finally:
        started.close()


@pytest.mark.parametrize("mode", ["chunked", "length"])
def test_analyze_upload_html_stops_at_html_cap(client, mode):
    size = HTML_CAP + OVERSHOOT
    prefix = html_page(None)
    started = serve({
        "/landing": Route(
            "text/html; charset=utf-8", size, prefix=prefix, mode=mode
        ),
    })
    try:
        resp = client.post(
            "/api/analyze/upload",
            json={"url": f"{started.base}/landing"},
        )
        assert_not_forbidden(resp)
        assert_capped(started.hits(), "/landing", HTML_CAP)
    finally:
        started.close()


@pytest.mark.parametrize("mode", ["chunked", "length"])
def test_analyze_upload_followed_pdf_stops_at_pdf_cap(client, mode):
    pdf_size = PDF_CAP + OVERSHOOT
    started = serve({})
    pdf_url = f"{started.base}/form.pdf"
    page = html_page(pdf_url)
    started.server.routes["/landing"] = Route(
        "text/html; charset=utf-8", len(page), prefix=page, mode="length"
    )
    started.server.routes["/form.pdf"] = Route(
        "application/pdf", pdf_size, mode=mode
    )
    try:
        resp = client.post(
            "/api/analyze/upload",
            json={"url": f"{started.base}/landing"},
        )
        assert_not_forbidden(resp)
        assert_fully_read(started.hits(), "/landing", len(page))
        assert_capped(started.hits(), "/form.pdf", PDF_CAP)
        assert_no_oversized_filesize(resp, PDF_CAP)
    finally:
        started.close()


def test_analyze_upload_reads_a_pdf_under_the_cap(client):
    size = 32 * 1024
    prefix = b"%PDF-1.4\n"
    started = serve({
        "/small.pdf": Route(
            "application/pdf", size, prefix=prefix, mode="chunked"
        ),
    })
    try:
        resp = client.post(
            "/api/analyze/upload",
            json={"url": f"{started.base}/small.pdf"},
        )
        assert_not_forbidden(resp)
        assert_fully_read(started.hits(), "/small.pdf", size)
    finally:
        started.close()


def test_analyze_upload_reads_html_under_the_cap(client):
    page = html_page(None)
    started = serve({
        "/landing": Route(
            "text/html; charset=utf-8", len(page), prefix=page, mode="chunked"
        ),
    })
    try:
        resp = client.post(
            "/api/analyze/upload",
            json={"url": f"{started.base}/landing"},
        )
        assert_not_forbidden(resp)
        assert_fully_read(started.hits(), "/landing", len(page))
    finally:
        started.close()


@pytest.mark.parametrize("mode", ["chunked", "length"])
def test_batch_create_direct_pdf_stops_at_pdf_cap(client, mode):
    size = PDF_CAP + OVERSHOOT
    started = serve({
        "/form.pdf": Route("application/pdf", size, mode=mode),
    })
    try:
        resp = client.post(
            "/api/batch/create",
            json={"url": f"{started.base}/form.pdf", "max_forms": 5},
        )
        assert_not_forbidden(resp)
        assert_capped(started.hits(), "/form.pdf", PDF_CAP)
    finally:
        started.close()


@pytest.mark.parametrize("mode", ["chunked", "length"])
def test_batch_create_html_page_stops_at_html_cap(client, mode):
    size = HTML_CAP + OVERSHOOT
    prefix = html_page(None)
    started = serve({
        "/landing": Route(
            "text/html; charset=utf-8", size, prefix=prefix, mode=mode
        ),
    })
    try:
        resp = client.post(
            "/api/batch/create",
            json={"url": f"{started.base}/landing", "max_forms": 5},
        )
        assert_not_forbidden(resp)
        assert_capped(started.hits(), "/landing", HTML_CAP)
    finally:
        started.close()


@pytest.mark.parametrize("mode", ["chunked", "length"])
def test_batch_create_linked_pdf_stops_at_pdf_cap(client, mode):
    pdf_size = PDF_CAP + OVERSHOOT
    started = serve({})
    pdf_url = f"{started.base}/form.pdf"
    page = html_page(pdf_url)
    started.server.routes["/landing"] = Route(
        "text/html; charset=utf-8", len(page), prefix=page, mode="length"
    )
    started.server.routes["/form.pdf"] = Route(
        "application/pdf", pdf_size, mode=mode
    )
    try:
        resp = client.post(
            "/api/batch/create",
            json={"url": f"{started.base}/landing", "max_forms": 5},
        )
        assert_not_forbidden(resp)
        assert_fully_read(started.hits(), "/landing", len(page))
        assert_capped(started.hits(), "/form.pdf", PDF_CAP)
    finally:
        started.close()


@pytest.mark.parametrize("mode", ["chunked", "length"])
def test_batch_create_robots_stops_at_html_cap(client, mode):
    size = HTML_CAP + OVERSHOOT
    page = html_page(None)
    started = serve({
        "/robots.txt": Route(
            "text/plain; charset=utf-8",
            size,
            prefix=b"User-agent: *\n",
            mode=mode,
        ),
        "/landing": Route(
            "text/html; charset=utf-8", len(page), prefix=page, mode="length"
        ),
    })
    try:
        resp = client.post(
            "/api/batch/create",
            json={"url": f"{started.base}/landing", "max_forms": 5},
        )
        assert_not_forbidden(resp)
        assert_capped(started.hits(), "/robots.txt", HTML_CAP)
        assert_fully_read(started.hits(), "/landing", len(page))
    finally:
        started.close()


@pytest.mark.parametrize("mode", ["chunked", "length"])
def test_batch_create_sitemap_stops_at_html_cap(client, mode):
    size = HTML_CAP + OVERSHOOT
    prefix = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    page = html_page(None)
    started = serve({
        "/sitemap.xml": Route(
            "text/xml; charset=utf-8", size, prefix=prefix, mode=mode
        ),
        "/landing": Route(
            "text/html; charset=utf-8", len(page), prefix=page, mode="length"
        ),
    })
    try:
        resp = client.post(
            "/api/batch/create",
            json={"url": f"{started.base}/landing", "max_forms": 5},
        )
        assert_not_forbidden(resp)
        assert_capped(started.hits(), "/sitemap.xml", HTML_CAP)
    finally:
        started.close()


def test_batch_create_reads_bodies_under_the_cap(client):
    page = html_page(None)
    robots = b"User-agent: *\nDisallow:\n"
    sitemap = (
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>http://example.test/form.pdf</loc></url></urlset>"
    )
    pdf = b"%PDF-1.4\n" + (b"\0" * 1024)
    started = serve({
        "/landing": Route(
            "text/html; charset=utf-8", len(page), prefix=page, mode="chunked"
        ),
        "/robots.txt": Route(
            "text/plain; charset=utf-8", len(robots), prefix=robots, mode="chunked"
        ),
        "/sitemap.xml": Route(
            "text/xml; charset=utf-8", len(sitemap), prefix=sitemap, mode="chunked"
        ),
        "/form.pdf": Route(
            "application/pdf", len(pdf), prefix=pdf, mode="chunked"
        ),
    })
    try:
        page_resp = client.post(
            "/api/batch/create",
            json={"url": f"{started.base}/landing", "max_forms": 5},
        )
        assert_not_forbidden(page_resp)
        hits = started.hits()
        assert_fully_read(hits, "/landing", len(page))
        assert_fully_read(hits, "/robots.txt", len(robots))
        assert_fully_read(hits, "/sitemap.xml", len(sitemap))

        pdf_resp = client.post(
            "/api/batch/create",
            json={"url": f"{started.base}/form.pdf", "max_forms": 5},
        )
        assert_not_forbidden(pdf_resp)
        pdf_hits = [
            hit for hit in started.hits()
            if hit.path == "/form.pdf" and hit not in hits
        ]
        assert pdf_hits, started.hits()
        assert pdf_hits[0].body_bytes == len(pdf)
    finally:
        started.close()
