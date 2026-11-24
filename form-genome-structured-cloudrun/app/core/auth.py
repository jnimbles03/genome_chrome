# app/core/auth.py
# Bearer-token authentication, per-identity daily quotas, and server-side
# ceilings for endpoints that trigger paid LLM analysis or fill the job queue.

import datetime
import hmac
import logging
import os
import threading
from functools import wraps
from typing import Dict, Optional

from flask import current_app, g, jsonify, request

logger = logging.getLogger(__name__)

DEFAULT_MAX_BATCH_URLS = 100
DEFAULT_MAX_DISCOVERY_FORMS = 200
DEFAULT_DAILY_UPLOADS = 50
DEFAULT_DAILY_QUEUED_JOBS = 500

_EXTENSION_KEY = 'formgenome_auth'
_init_lock = threading.Lock()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.error(f"{name}={raw!r} is not an integer; using {default}")
        return default
    return max(value, 0)


def parse_api_tokens(raw: str) -> Dict[str, str]:
    """Parse FORMGENOME_API_TOKENS: comma-separated ``token:identity`` pairs."""
    tokens: Dict[str, str] = {}
    for entry in (raw or '').split(','):
        entry = entry.strip()
        if not entry:
            continue
        token, sep, identity = entry.rpartition(':')
        token, identity = token.strip(), identity.strip()
        if not sep or not token or not identity:
            logger.error("Ignoring malformed FORMGENOME_API_TOKENS entry (expected token:identity)")
            continue
        tokens[token] = identity
    return tokens


class DailyQuota:
    """Per-identity counter that resets at UTC midnight.

    ``reserve`` checks and consumes in one locked step, so concurrent requests
    cannot both take the last remaining slot. Counters live in this process.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self._lock = threading.Lock()
        self._day: Optional[datetime.date] = None
        self._used: Dict[str, int] = {}

    def _roll(self):
        today = datetime.datetime.now(datetime.timezone.utc).date()
        if self._day != today:
            self._day = today
            self._used = {}

    def reserve(self, identity: str, amount: int = 1) -> bool:
        if amount < 0:
            return False
        with self._lock:
            self._roll()
            used = self._used.get(identity, 0)
            if used + amount > self.limit:
                return False
            self._used[identity] = used + amount
            return True

    def release(self, identity: str, amount: int):
        if amount <= 0:
            return
        with self._lock:
            self._roll()
            used = self._used.get(identity, 0)
            self._used[identity] = max(used - amount, 0)

    def remaining(self, identity: str) -> int:
        with self._lock:
            self._roll()
            return max(self.limit - self._used.get(identity, 0), 0)


class AuthSettings:
    def __init__(self):
        self.tokens = parse_api_tokens(os.getenv('FORMGENOME_API_TOKENS', ''))
        self.max_batch_urls = _int_env('FORMGENOME_MAX_BATCH_URLS', DEFAULT_MAX_BATCH_URLS)
        self.max_discovery_forms = _int_env('FORMGENOME_MAX_DISCOVERY_FORMS', DEFAULT_MAX_DISCOVERY_FORMS)
        self.upload_quota = DailyQuota(_int_env('FORMGENOME_DAILY_UPLOADS', DEFAULT_DAILY_UPLOADS))
        self.job_quota = DailyQuota(_int_env('FORMGENOME_DAILY_QUEUED_JOBS', DEFAULT_DAILY_QUEUED_JOBS))

    def identify(self, presented: str) -> Optional[str]:
        identity = None
        presented_bytes = presented.encode('utf-8')
        for token, name in self.tokens.items():
            if hmac.compare_digest(presented_bytes, token.encode('utf-8')):
                identity = name
        return identity


def init_auth(app) -> AuthSettings:
    """Load token and quota settings onto the app. Call from create_app."""
    with _init_lock:
        settings = app.extensions.get(_EXTENSION_KEY)
        if settings is None:
            settings = AuthSettings()
            app.extensions[_EXTENSION_KEY] = settings
            if not settings.tokens:
                logger.warning(
                    "FORMGENOME_API_TOKENS is empty: analysis and batch endpoints will reject every request"
                )
        return settings


def auth_settings() -> AuthSettings:
    settings = current_app.extensions.get(_EXTENSION_KEY)
    if settings is None:
        settings = init_auth(current_app)
    return settings


def _unauthorized(message: str):
    response = jsonify({"success": False, "ok": False, "error": message})
    response.status_code = 401
    response.headers['WWW-Authenticate'] = 'Bearer realm="formgenome"'
    return response


def require_api_token(view):
    """Reject the request unless it carries a known ``Authorization: Bearer`` token.

    On success the caller's identity is bound to ``g.api_identity``.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get('Authorization', '')
        scheme, _, presented = header.partition(' ')
        presented = presented.strip()
        if scheme.lower() != 'bearer' or not presented:
            return _unauthorized("Authentication required")
        identity = auth_settings().identify(presented)
        if identity is None:
            return _unauthorized("Invalid API token")
        g.api_identity = identity
        return view(*args, **kwargs)
    return wrapper


def quota_exceeded(message: str, remaining: int):
    response = jsonify({"success": False, "ok": False, "error": message, "remaining": remaining})
    response.status_code = 429
    return response
