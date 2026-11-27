# app/services/remote_fetch.py
# Size- and time-bounded HTTP downloads for URLs that API callers control.
from __future__ import annotations

import io
import time
import zlib
from typing import Iterable, Optional
from urllib.parse import urljoin

import requests
from requests.cookies import extract_cookies_to_jar
from requests.exceptions import (
    ChunkedEncodingError,
    ConnectionError as RequestsConnectionError,
    ContentDecodingError,
    SSLError as RequestsSSLError,
    Timeout,
    TooManyRedirects,
)
from urllib3.exceptions import (
    DecodeError,
    ProtocolError,
    ReadTimeoutError,
    SSLError as Urllib3SSLError,
)

__all__ = [
    "MAX_HTML_BYTES",
    "MAX_PDF_BYTES",
    "ResponseTooLarge",
    "open_stream",
    "read_capped",
    "get_capped",
]

MAX_HTML_BYTES = 5_000_000
MAX_PDF_BYTES = 50 * 1024 * 1024
DEFAULT_TOTAL_TIMEOUT = 60.0
MAX_REDIRECTS = 10

_CHUNK_SIZE = 16 * 1024
# Only codings we can decode with a bounded output size are advertised, so a
# compressed body cannot expand past the cap inside a single read.
_ACCEPT_ENCODING = "gzip, deflate"
_DEADLINE_ATTR = "_remote_fetch_deadline"


class ResponseTooLarge(requests.RequestException):
    """The response body is larger than the caller's limit."""


def open_stream(
    url: str,
    *,
    session: Optional[requests.Session] = None,
    headers: Optional[dict] = None,
    timeout=20,
    total_timeout: float = DEFAULT_TOTAL_TIMEOUT,
    max_redirects: int = MAX_REDIRECTS,
    retry_statuses: Iterable[int] = (),
    retries: int = 0,
    backoff: float = 0.5,
) -> requests.Response:
    """GET ``url`` and return the response with its body still unread.

    Redirects and status retries are handled here rather than by requests or
    urllib3, because both of those read the entire body of the intermediate
    response into memory before moving on. Intermediate responses are closed
    unread instead. Sessions passed in must not use a urllib3 ``Retry`` with
    ``status_forcelist`` or ``respect_retry_after_header`` for the same reason.

    The caller must either pass the response to ``read_capped`` or close it.
    """
    deadline = time.monotonic() + total_timeout
    req_headers = dict(headers or {})
    req_headers["Accept-Encoding"] = _ACCEPT_ENCODING
    if session is None:
        with requests.Session() as own:
            return _open(own, url, req_headers, timeout, deadline,
                         max_redirects, tuple(retry_statuses), retries, backoff)
    return _open(session, url, req_headers, timeout, deadline,
                 max_redirects, tuple(retry_statuses), retries, backoff)


def _open(session, url, headers, timeout, deadline, max_redirects,
          retry_statuses, retries, backoff) -> requests.Response:
    redirects = 0
    attempts = 0
    while True:
        _check_deadline(deadline, url)
        resp = _send(session, url, headers, timeout)
        target = session.get_redirect_target(resp)
        if target:
            resp.close()
            redirects += 1
            if redirects > max_redirects:
                raise TooManyRedirects(
                    f"Exceeded {max_redirects} redirects", response=resp)
            url = urljoin(resp.url, target)
            continue
        if resp.status_code in retry_statuses and attempts < retries:
            delay = backoff * (2 ** attempts)
            if time.monotonic() + delay < deadline:
                resp.close()
                attempts += 1
                time.sleep(delay)
                continue
        setattr(resp, _DEADLINE_ATTR, deadline)
        return resp


def _send(session, url, headers, timeout) -> requests.Response:
    # Session.send is bypassed: even with allow_redirects=False it reads the
    # body of a 3xx response to precompute Response.next.
    prep = session.prepare_request(requests.Request("GET", url, headers=headers))
    settings = session.merge_environment_settings(prep.url, {}, True, None, None)
    resp = session.get_adapter(prep.url).send(prep, timeout=timeout, **settings)
    extract_cookies_to_jar(session.cookies, prep, resp.raw)
    return resp


def read_capped(resp: requests.Response, max_bytes: int,
                truncate: bool = False) -> bytes:
    """Read at most ``max_bytes`` of decoded body from a streamed response.

    Raises ``ResponseTooLarge`` once the body passes the limit, or keeps only
    the first ``max_bytes`` when ``truncate`` is set. Always closes ``resp``.
    Afterwards ``resp.content`` and ``resp.text`` return the bytes read.
    """
    try:
        declared = (resp.headers.get("Content-Length") or "").strip()
        if not truncate and declared.isdigit() and int(declared) > max_bytes:
            raise ResponseTooLarge(
                f"Response too large: {declared} bytes (max {max_bytes})")

        decoder = _decoder_for(resp.headers.get("Content-Encoding", ""))
        deadline = getattr(resp, _DEADLINE_ATTR, None)
        buf = io.BytesIO()
        raw_total = 0
        total = 0
        while True:
            _check_deadline(deadline, resp.url)
            data = _read_raw(resp, _CHUNK_SIZE)
            if not data:
                break
            raw_total += len(data)
            if decoder is not None:
                data = decoder.feed(data, max_bytes - total + 1)
            if total + len(data) > max_bytes or raw_total > max_bytes:
                if not truncate:
                    raise ResponseTooLarge(
                        f"Response too large: >{max_bytes} bytes")
                buf.write(data[: max(0, max_bytes - total)])
                break
            buf.write(data)
            total += len(data)
        body = buf.getvalue()
    finally:
        resp.close()
    resp._content = body
    resp._content_consumed = True
    return body


def get_capped(url: str, max_bytes: int, truncate: bool = False,
               **kwargs) -> requests.Response:
    """``open_stream`` followed by ``read_capped``; the body is in ``.content``."""
    resp = open_stream(url, **kwargs)
    read_capped(resp, max_bytes, truncate=truncate)
    return resp


def _check_deadline(deadline: Optional[float], url: str) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise Timeout(f"Download exceeded its time budget: {url}")


def _read_raw(resp: requests.Response, amt: int) -> bytes:
    try:
        return resp.raw.read(amt, decode_content=False)
    except ProtocolError as e:
        raise ChunkedEncodingError(e)
    except DecodeError as e:
        raise ContentDecodingError(e)
    except ReadTimeoutError as e:
        raise RequestsConnectionError(e)
    except Urllib3SSLError as e:
        raise RequestsSSLError(e)


def _decoder_for(content_encoding: str) -> Optional["_BoundedDecoder"]:
    codings = [c.strip().lower() for c in content_encoding.split(",")]
    codings = [c for c in codings if c and c != "identity"]
    if not codings:
        return None
    if len(codings) == 1 and codings[0] in ("gzip", "x-gzip", "deflate"):
        return _BoundedDecoder(gzip=codings[0] != "deflate")
    raise ContentDecodingError(
        f"Unsupported Content-Encoding: {content_encoding}")


class _BoundedDecoder:
    """gzip/deflate decoder whose output per call never exceeds ``limit``."""

    def __init__(self, gzip: bool):
        self._gzip = gzip
        self._started = False
        self._extra_member = False
        self._finished = False
        self._obj = self._new(zlib.MAX_WBITS)

    def _new(self, wbits: int):
        return zlib.decompressobj(16 + wbits if self._gzip else wbits)

    def feed(self, data: bytes, limit: int) -> bytes:
        out = bytearray()
        while data and not self._finished and len(out) < limit:
            if self._obj.eof:
                if not self._gzip:
                    self._finished = True
                    break
                self._obj = self._new(zlib.MAX_WBITS)
                self._extra_member = True
            try:
                chunk = self._obj.decompress(data, limit - len(out))
            except zlib.error as e:
                if not self._gzip and not self._started:
                    # Some servers send raw deflate without the zlib header.
                    self._obj = self._new(-zlib.MAX_WBITS)
                    self._started = True
                    continue
                if self._extra_member:
                    # Trailing bytes after a complete gzip member are ignored.
                    self._finished = True
                    break
                raise ContentDecodingError(e)
            self._started = True
            out += chunk
            if self._obj.unconsumed_tail:
                break
            data = self._obj.unused_data if self._obj.eof else b""
        return bytes(out)
