# app/api/team_auth.py
# Bearer-token authentication for the team API (/api/team/*)
#
# Tokens are compact JWTs signed with HMAC-SHA256 using the Flask SECRET_KEY.
# Required claims: sub (user id), role ("user" or "admin"), exp (unix seconds).

import base64
import hashlib
import hmac
import json
import math
import time
from typing import Dict, Optional

TEAM_ROLES = ("user", "admin")

# SECRET_KEY values that are published in this repository's history and
# therefore cannot protect token signatures.
_PUBLIC_SECRET_KEYS = frozenset({"dev-secret-key-change-in-production"})
_MIN_SECRET_KEY_LENGTH = 16


class TeamAuthError(Exception):
    """The request does not carry a valid team API token."""


class TeamAuthNotConfigured(Exception):
    """The server has no usable SECRET_KEY to verify tokens with."""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _usable_secret(secret_key) -> str:
    if (
        not isinstance(secret_key, str)
        or len(secret_key) < _MIN_SECRET_KEY_LENGTH
        or secret_key in _PUBLIC_SECRET_KEYS
    ):
        raise TeamAuthNotConfigured("SECRET_KEY is unset or insecure")
    return secret_key


def _sign(signing_input: str, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()


def issue_token(sub: str, role: str, secret_key: str, ttl_seconds: int) -> str:
    """Create a signed team API token (used by mint_team_token.py)."""
    if not isinstance(sub, str) or not sub:
        raise ValueError("sub must be a non-empty string")
    if role not in TEAM_ROLES:
        raise ValueError(f"role must be one of {TEAM_ROLES}")
    secret = _usable_secret(secret_key)
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {"sub": sub, "role": role, "exp": int(time.time()) + int(ttl_seconds)}
    signing_input = ".".join(
        _b64url_encode(json.dumps(part, separators=(",", ":")).encode("utf-8"))
        for part in (header, claims)
    )
    return signing_input + "." + _b64url_encode(_sign(signing_input, secret))


def verify_token(token: str, secret_key) -> Dict[str, str]:
    """Return {"sub", "role"} for a valid token, else raise TeamAuthError."""
    secret = _usable_secret(secret_key)
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = json.loads(_b64url_decode(header_b64))
        if not isinstance(header, dict) or header.get("alg") != "HS256":
            raise TeamAuthError("unsupported token algorithm")
        expected = _sign(header_b64 + "." + payload_b64, secret)
        if not hmac.compare_digest(expected, _b64url_decode(signature_b64)):
            raise TeamAuthError("bad token signature")
        claims = json.loads(_b64url_decode(payload_b64))
    except TeamAuthError:
        raise
    except (ValueError, TypeError, AttributeError, RecursionError) as exc:
        raise TeamAuthError("malformed token") from exc

    if not isinstance(claims, dict):
        raise TeamAuthError("malformed token claims")

    sub = claims.get("sub")
    role = claims.get("role")
    exp = claims.get("exp")
    if not isinstance(sub, str) or not sub:
        raise TeamAuthError("token has no subject")
    if role not in TEAM_ROLES:
        raise TeamAuthError("token has no valid role")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or not math.isfinite(exp):
        raise TeamAuthError("token has no valid expiry")
    if exp <= time.time():
        raise TeamAuthError("token expired")
    return {"sub": sub, "role": role}


def bearer_token(authorization_header: Optional[str]) -> str:
    if not authorization_header:
        raise TeamAuthError("missing Authorization header")
    scheme, _, value = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise TeamAuthError("Authorization header is not a bearer token")
    return value.strip()
