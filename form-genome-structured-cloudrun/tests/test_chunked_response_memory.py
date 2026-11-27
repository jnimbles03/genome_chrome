"""Caller-supplied URLs must not be able to force an unbounded allocation.

urllib3 2.1.0 (pinned in requirements.txt) reads a chunked response body
through HTTPResponse.stream and HTTPResponse.read_chunked. That parser
buffers the chunk-size line until newline or EOF with no length cap. These
tests point the product's real fetchers at a local server that answers
``Transfer-Encoding: chunked`` and then a long run of bytes with no newline.

They fail while that parser is unbounded: peak traced allocation during the
fetch grows with the attacker's line. They pass once the chunk-size line is
rejected at a fixed cap (the behavior in urllib3 2.8.0) and the product no
longer retains a buffer the size of the line.

Run from form-genome-structured-cloudrun, with that directory's dependencies
installed (including the pinned urllib3):

    pip install -r requirements.txt
    python -m pytest tests/test_chunked_response_memory.py -q

Covered fetchers: analyze_url_logic (the caller's URL and the PDF it follows),
POST /api/analyze/upload, download_pdf (streamed iter_content, including the
batch worker's process_job path), crawler robots.txt, sitemap guesses, sitemap
bodies, HTML pages, and linked PDFs, and llm_discover.fetch.

Not covered here: the session.get inside EnhancedWebCrawler._harvest_via_playwright
(same requests body read, reached only after Playwright launches). Google and
Bing search helpers are not attacker-controlled servers. HEAD requests do not
read a chunked body. aiohttp is declared and unused.
"""

import contextlib
import io
import logging
import socket
import sys
import threading
import tracemalloc
from pathlib import Path

import pytest
import requests
from bs4 import BeautifulSoup
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import the fetchers at collection time. analyze_url_logic imports requests
# and BeautifulSoup on first use; that cost must not sit inside the traced
# window the assertions measure.
from app.api.analyze import analyze_bp, analyze_url_logic
from app.services.batch_processor import download_pdf
from app.services.enhanced_crawler import EnhancedWebCrawler
from app.services.llm_discover import fetch as discover_fetch

# Keep the failure output small. The vulnerable parser puts the whole
# chunk-size line into the exception text, and the app interpolates that
# text into log messages.
logging.disable(logging.CRITICAL)

# Larger than urllib3 2.8.0's chunk-line cap (64 KiB) and larger than the
# allocation those fetches make for a rejected line, including the short
# error string. Far smaller than the line this server sends.
FLOOD_SIZE = 1024 * 1024
MAX_PEAK_BYTES = 512 * 1024
FLOOD = b"Z" * FLOOD_SIZE

_RESPONSE_HEAD = (
    b"HTTP/1.1 200 OK\r\n"
    b"Transfer-Encoding: chunked\r\n"
    b"Connection: close\r\n"
)


def _read_http_request(conn):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        data += chunk
        if len(data) > 65536:
            break
    return data


def _request_parts(data):
    line = data.split(b"\r\n", 1)[0]
    parts = line.split()
    method = parts[0].decode("ascii", "replace") if parts else ""
    target = parts[1].decode("ascii", "replace") if len(parts) > 1 else "/"
    path = target.split("?", 1)[0]
    return method, path


def _send_flood(conn, content_type):
    conn.sendall(_RESPONSE_HEAD)
    conn.sendall(b"Content-Type: " + content_type + b"\r\n\r\n")
    conn.sendall(FLOOD)


def _send_chunked_body(conn, content_type, body):
    conn.sendall(_RESPONSE_HEAD)
    conn.sendall(b"Content-Type: " + content_type + b"\r\n\r\n")
    conn.sendall(b"%x\r\n" % len(body))
    conn.sendall(body)
    conn.sendall(b"\r\n0\r\n\r\n")


def _send_head_only(conn, content_type):
    conn.sendall(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: " + content_type + b"\r\n"
        b"Content-Length: 0\r\n"
        b"Connection: close\r\n\r\n"
    )


class _LocalServer:
    """HTTP server on 127.0.0.1. ``mode`` selects which paths send the flood."""

    def __init__(self, mode):
        self.mode = mode
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(32)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def close(self):
        self._stop.set()
        try:
            socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
        except OSError:
            pass
        self.sock.close()
        self._thread.join(timeout=2)

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                self.sock.settimeout(0.3)
                conn, _addr = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            raw = _read_http_request(conn)
            if not raw:
                return
            method, path = _request_parts(raw)
            self._respond(conn, method, path)
        except OSError:
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _respond(self, conn, method, path):
        if self.mode == "flood":
            if method == "HEAD":
                _send_head_only(conn, b"text/html")
                return
            content_type = b"application/pdf" if path.endswith(".pdf") else b"text/html"
            _send_flood(conn, content_type)
            return

        # Valid HTML that links to a PDF, then the flood on that PDF.
        # HEAD of the PDF advertises application/pdf so the crawler will GET it.
        if path.endswith(".pdf"):
            if method == "HEAD":
                _send_head_only(conn, b"application/pdf")
            else:
                _send_flood(conn, b"application/pdf")
            return
        html = (
            "<html><body><a href=\"%s\">form</a></body></html>"
            % self.url("/form.pdf")
        ).encode("ascii")
        _send_chunked_body(conn, b"text/html", html)


@pytest.fixture(scope="session", autouse=True)
def _warm_fetcher_imports():
    """Load libraries the fetchers import on first use, outside the measurement."""
    assert requests.__version__
    assert BeautifulSoup("<p>ok</p>", "html.parser").get_text() == "ok"
    assert Flask
    assert analyze_url_logic and download_pdf and EnhancedWebCrawler and discover_fetch
    assert analyze_bp.name


@pytest.fixture
def flood_server():
    server = _LocalServer("flood")
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def html_then_pdf_server():
    server = _LocalServer("html-then-pdf")
    try:
        yield server
    finally:
        server.close()


def _peak_during(fn):
    """Peak traced bytes while ``fn`` runs.

    Fetch errors are part of the scenario: a bounded parser rejects the
    chunk-size line. AssertionError is kept so a check inside ``fn`` still
    fails the test.
    """
    sink = io.StringIO()
    tracemalloc.start()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                fn()
            except AssertionError:
                raise
            except Exception:
                pass
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def _assert_chunk_line_is_bounded(fn):
    peak = _peak_during(fn)
    assert peak < MAX_PEAK_BYTES, (
        "reading an unterminated %d-byte chunk-size line allocated %d bytes; "
        "the chunk-size line must be rejected before a buffer that size exists"
        % (FLOOD_SIZE, peak)
    )


def test_analyze_url_rejects_unbounded_chunk_size_line(flood_server):
    from app.api.analyze import analyze_url_logic

    _assert_chunk_line_is_bounded(
        lambda: analyze_url_logic(flood_server.url("/form.pdf"))
    )


def test_analyze_followed_pdf_rejects_unbounded_chunk_size_line(html_then_pdf_server):
    from app.api.analyze import analyze_url_logic

    _assert_chunk_line_is_bounded(
        lambda: analyze_url_logic(html_then_pdf_server.url("/landing"))
    )


def test_analyze_endpoint_rejects_unbounded_chunk_size_line(flood_server):
    """POST /api/analyze/upload takes a URL from any caller and fetches it."""
    from flask import Flask

    from app.api.analyze import analyze_bp

    app = Flask(__name__)
    app.register_blueprint(analyze_bp)
    client = app.test_client()

    def post_url():
        response = client.post(
            "/api/analyze/upload",
            json={"url": flood_server.url("/form.pdf")},
        )
        # Status only. The JSON error string contains the chunk-size line
        # while the bug exists, so this does not read the body.
        assert response.status_code != 200

    _assert_chunk_line_is_bounded(post_url)


def test_download_pdf_rejects_unbounded_chunk_size_line(flood_server):
    from app.services.batch_processor import download_pdf

    _assert_chunk_line_is_bounded(
        lambda: download_pdf(
            flood_server.url("/form.pdf"),
            timeout=5,
            max_size=4096,
        )
    )


def test_crawler_robots_and_sitemaps_reject_unbounded_chunk_size_line(flood_server):
    from app.services.enhanced_crawler import EnhancedWebCrawler

    crawler = EnhancedWebCrawler(
        config={
            "enable_playwright": False,
            "per_host_delay": 0,
            "timeout": 2,
            "read_timeout": 2,
            "max_seconds": 10,
        }
    )
    _assert_chunk_line_is_bounded(
        lambda: crawler._fetch_robots_and_sitemaps(flood_server.url("/start"))
    )


def test_crawler_sitemap_body_rejects_unbounded_chunk_size_line(flood_server):
    from app.services.enhanced_crawler import EnhancedWebCrawler

    crawler = EnhancedWebCrawler(
        config={
            "enable_playwright": False,
            "per_host_delay": 0,
            "timeout": 2,
            "read_timeout": 2,
            "max_seconds": 10,
        }
    )
    _assert_chunk_line_is_bounded(
        lambda: crawler._seed_from_sitemaps([flood_server.url("/sitemap.xml")])
    )


def test_crawler_page_rejects_unbounded_chunk_size_line(flood_server):
    from app.services.enhanced_crawler import EnhancedWebCrawler

    crawler = EnhancedWebCrawler(
        config={
            "enable_playwright": False,
            "per_host_delay": 0,
            "timeout": 2,
            "read_timeout": 2,
            "max_seconds": 10,
        }
    )
    _assert_chunk_line_is_bounded(
        lambda: crawler._crawl_recursive(flood_server.url("/page"), 0, 1, 5)
    )


def test_crawler_linked_pdf_rejects_unbounded_chunk_size_line(html_then_pdf_server):
    from app.services.enhanced_crawler import EnhancedWebCrawler

    crawler = EnhancedWebCrawler(
        config={
            "enable_playwright": False,
            "per_host_delay": 0,
            "timeout": 2,
            "read_timeout": 2,
            "max_seconds": 10,
        }
    )
    _assert_chunk_line_is_bounded(
        lambda: crawler._crawl_recursive(html_then_pdf_server.url("/landing"), 0, 1, 5)
    )


def test_llm_discover_fetch_rejects_unbounded_chunk_size_line(flood_server):
    from app.services.llm_discover import fetch

    _assert_chunk_line_is_bounded(
        lambda: fetch(flood_server.url("/page"), timeout=5)
    )
