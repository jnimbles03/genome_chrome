# app/services/safe_fetch.py
# Outbound HTTP for user-supplied URLs.
#
# Every hop (including redirects) is resolved once, every resolved address must
# be publicly routable, and the socket is opened to one of those validated
# addresses. The hostname is never handed to a resolver again, so a second DNS
# answer cannot move the connection to an internal address.

import ipaddress
import logging
import socket
from typing import List, Mapping, Optional, Tuple, Union
from urllib.parse import urljoin, urlsplit

import certifi
from requests.utils import requote_uri
from urllib3 import HTTPConnectionPool, HTTPSConnectionPool
from urllib3._collections import HTTPHeaderDict
from urllib3.exceptions import (
    ConnectTimeoutError,
    HTTPError as Urllib3HTTPError,
    MaxRetryError,
    NewConnectionError,
)
from urllib3.util import Retry, Timeout

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = ("http", "https")
DEFAULT_PORTS = {"http": 80, "https": 443}
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_REDIRECTS = 10
REDIRECT_STATUSES = (301, 302, 303, 307, 308)

_NAT64_NETWORKS = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)

TimeoutArg = Union[None, float, Tuple[float, float]]


class FetchError(Exception):
    """Outbound fetch failed."""


class BlockedDestinationError(FetchError):
    """URL is not an allowed public http(s) destination."""


class ResponseTooLargeError(FetchError):
    """Response body exceeded the configured size cap."""


class SafeResponse:
    def __init__(self, url: str, status_code: int, headers: HTTPHeaderDict, content: bytes):
        self.url = url
        self.status_code = status_code
        self.headers = headers
        self.content = content

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    @property
    def text(self) -> str:
        charset = None
        ctype = self.headers.get("Content-Type", "")
        for part in ctype.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                charset = value.strip("\"'")
        try:
            return self.content.decode(charset or "utf-8", errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> List[ipaddress.IPv4Address]:
    found = []
    if ip.ipv4_mapped:
        found.append(ip.ipv4_mapped)
    if ip.sixtofour:
        found.append(ip.sixtofour)
    if ip.teredo:
        found.extend(ip.teredo)
    if any(ip in net for net in _NAT64_NETWORKS):
        found.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return found


def is_public_address(value: Union[str, ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0] if isinstance(value, str) else value)
    except ValueError:
        return False
    if not ip.is_global or ip.is_multicast or ip.is_reserved:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        return all(is_public_address(v4) for v4 in _embedded_ipv4(ip))
    return True


class _Target:
    __slots__ = ("url", "scheme", "hostname", "port", "host_header", "request_target", "addresses")

    def __init__(self, url, scheme, hostname, port, host_header, request_target, addresses):
        self.url = url
        self.scheme = scheme
        self.hostname = hostname
        self.port = port
        self.host_header = host_header
        self.request_target = request_target
        self.addresses = addresses


def _resolve_public(hostname: str, port: int) -> List[str]:
    try:
        infos = socket.getaddrinfo(hostname, port, 0, socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, ValueError) as exc:
        raise FetchError("Could not resolve the URL's host") from exc

    addresses: List[str] = []
    for family, _type, _proto, _canon, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            raise BlockedDestinationError("URL is not allowed: unsupported address family")
        ip = sockaddr[0]
        if not is_public_address(ip):
            raise BlockedDestinationError("URL is not allowed: destination is not a public address")
        if ip not in addresses:
            addresses.append(ip)
    if not addresses:
        raise FetchError("Could not resolve the URL's host")
    return addresses


def validate_url(url: str) -> _Target:
    """Parse url, resolve its host once, and require every address to be public."""
    if not isinstance(url, str) or not url.strip():
        raise BlockedDestinationError("URL is not allowed: empty URL")
    url = requote_uri(url.strip())
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise BlockedDestinationError("URL is not allowed: malformed URL") from exc

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise BlockedDestinationError("URL is not allowed: only http and https are supported")
    if parts.username is not None or parts.password is not None:
        raise BlockedDestinationError("URL is not allowed: credentials in URLs are not supported")
    hostname = parts.hostname
    if not hostname:
        raise BlockedDestinationError("URL is not allowed: missing host")
    if port is None:
        port = DEFAULT_PORTS[scheme]

    addresses = _resolve_public(hostname, port)

    request_target = parts.path or "/"
    if parts.query:
        request_target += "?" + parts.query
    return _Target(
        url=url,
        scheme=scheme,
        hostname=hostname,
        port=port,
        host_header=parts.netloc,
        request_target=request_target,
        addresses=addresses,
    )


def _make_pool(target: _Target, address: str, timeout: Timeout, retries):
    if target.scheme == "https":
        return HTTPSConnectionPool(
            host=address,
            port=target.port,
            timeout=timeout,
            retries=retries,
            maxsize=1,
            cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where(),
            server_hostname=target.hostname,
            assert_hostname=target.hostname,
        )
    return HTTPConnectionPool(
        host=address,
        port=target.port,
        timeout=timeout,
        retries=retries,
        maxsize=1,
    )


def _read_capped(resp, max_bytes: int) -> bytes:
    declared = resp.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise ResponseTooLargeError(f"Response too large: {int(declared)} bytes (max {max_bytes})")
    chunks = []
    total = 0
    for chunk in resp.stream(64 * 1024, decode_content=True):
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLargeError(f"Response too large: >{max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _open(target: _Target, method: str, headers: Mapping[str, str], body, timeout: Timeout,
          retries, max_bytes: int, read_body: bool):
    send_headers = HTTPHeaderDict()
    send_headers["Accept-Encoding"] = "gzip, deflate"
    for key, value in (headers or {}).items():
        if key.lower() != "host":
            send_headers[key] = value
    send_headers["Host"] = target.host_header

    last_exc: Optional[Exception] = None
    for address in target.addresses:
        pool = _make_pool(target, address, timeout, retries)
        try:
            resp = pool.urlopen(
                method,
                target.request_target,
                body=body,
                headers=send_headers,
                redirect=False,
                assert_same_host=False,
                preload_content=False,
                decode_content=True,
            )
        except (NewConnectionError, ConnectTimeoutError) as exc:
            last_exc = exc
            pool.close()
            continue
        except MaxRetryError as exc:
            pool.close()
            if isinstance(exc.reason, (NewConnectionError, ConnectTimeoutError)):
                last_exc = exc
                continue
            raise FetchError(f"Request failed: {exc.reason.__class__.__name__}") from exc
        except Urllib3HTTPError as exc:
            pool.close()
            raise FetchError(f"Request failed: {exc.__class__.__name__}") from exc
        try:
            content = _read_capped(resp, max_bytes) if read_body else b""
        except Urllib3HTTPError as exc:
            raise FetchError(f"Reading response failed: {exc.__class__.__name__}") from exc
        finally:
            resp.release_conn()
            pool.close()
        return resp.status, resp.headers, content
    raise FetchError("Could not connect to the URL's host") from last_exc


def safe_request(
    method: str,
    url: str,
    headers: Optional[Mapping[str, str]] = None,
    timeout: TimeoutArg = 20.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allow_redirects: bool = True,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    body: Optional[bytes] = None,
    retries: Union[Retry, bool, int, None] = False,
) -> SafeResponse:
    """Fetch a user-supplied URL, refusing non-public destinations on every hop."""
    method = method.upper()
    if isinstance(timeout, tuple):
        urllib3_timeout = Timeout(connect=timeout[0], read=timeout[1])
    else:
        urllib3_timeout = Timeout(connect=timeout, read=timeout)
    if isinstance(retries, Retry):
        retries = retries.new(redirect=False)

    current = url
    for _hop in range(max_redirects + 1):
        target = validate_url(current)
        status, resp_headers, content = _open(
            target, method, headers or {}, body, urllib3_timeout, retries, max_bytes,
            read_body=method != "HEAD",
        )
        location = resp_headers.get("Location")
        if allow_redirects and status in REDIRECT_STATUSES and location:
            current = urljoin(target.url, location)
            if status == 303 or (status in (301, 302) and method not in ("GET", "HEAD")):
                method = "GET"
                body = None
            continue
        return SafeResponse(target.url, status, resp_headers, content)
    raise FetchError("Too many redirects")


def safe_get(url: str, **kwargs) -> SafeResponse:
    return safe_request("GET", url, **kwargs)


def safe_head(url: str, **kwargs) -> SafeResponse:
    return safe_request("HEAD", url, **kwargs)
