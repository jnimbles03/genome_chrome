# app/api/auth.py
# Bearer-token client authentication for endpoints that spend LLM or queue capacity

import hashlib
import hmac
import logging
from functools import wraps
from typing import Optional

from flask import g, jsonify, request

from app.core.config import Config

logger = logging.getLogger(__name__)


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def authenticate_client(authorization: Optional[str]) -> Optional[str]:
    """Return the client id for a valid ``Bearer`` credential, else None.

    The client id is a SHA-256 of the token so the raw token is never stored
    or logged. Every configured token is compared in constant time.
    """
    if not authorization:
        return None
    scheme, _, credential = authorization.partition(" ")
    credential = credential.strip()
    if scheme.lower() != "bearer" or not credential:
        return None

    supplied = _digest(credential)
    matched = None
    for token in Config.API_TOKENS:
        expected = _digest(token)
        if hmac.compare_digest(supplied, expected):
            matched = expected
    return matched.hex() if matched else None


def require_client(view):
    """Refuse the request before the view runs unless it carries a known token.

    The view must not read the request body before this check, so nothing is
    downloaded, parsed, stored or sent to an LLM for an unauthenticated caller.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not Config.API_TOKENS:
            logger.error("FORMGENOME_API_TOKENS is not configured; refusing %s", request.path)
        client_id = authenticate_client(request.headers.get("Authorization"))
        if client_id is None:
            response = jsonify({"success": False, "ok": False, "error": "Missing or invalid API token"})
            response.status_code = 401
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
        g.client_id = client_id
        return view(*args, **kwargs)
    return wrapper
