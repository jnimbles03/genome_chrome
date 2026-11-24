"""Authorization contract for the team form API.

These tests exercise the Flask routes registered by ``create_app``:

- GET  /api/team/forms
- GET  /api/records_unique          (same handler as the list route)
- POST /api/team/sync
- POST /api/team/update
- POST /api/team/delete
- POST /api/team/reanalyze

They fail while those handlers perform the database read or write without
authentication. A fix should make this file pass without weakening the
assertions.

Authentication
--------------
Every route above requires a bearer token:

    Authorization: Bearer <jwt>

The JWT is the compact serialization (RFC 7519) signed with HMAC-SHA256.
The key is the Flask ``SECRET_KEY`` (the ``SECRET_KEY`` environment variable
``create_app`` copies into ``app.config['SECRET_KEY']``). The header ``alg``
must be exactly ``HS256``. Tokens with ``alg`` of ``none``, a bad signature,
a missing ``exp``, or an ``exp`` that is not in the future are rejected.

Required payload claims:

- ``sub`` (string): the acting user id. The server derives the user from
  this claim. ``user_id`` in the JSON body, a query parameter, or any
  client header is not an identity.
- ``role`` (string): ``user`` or ``admin``.
- ``exp`` (number): unix expiry, in seconds. Reject the token when it is
  absent or not strictly in the future.

Missing or invalid credentials: HTTP 401. The database is unchanged and the
response body does not contain stored form URLs.

What an authenticated caller may do
-----------------------------------
Records are the ``forms`` rows written by ``save_form``. The owner is
``metadata.user_id`` (JSON stored in the metadata column), which is the
token ``sub`` of the caller who synced the form.

- List (``/api/team/forms`` and ``/api/records_unique``): any authenticated
  user or admin may read the team list. This is the shared team dashboard.
- Update: the caller (user or admin) may change a row only when its
  ``metadata.user_id`` equals the token ``sub``. The id written by the
  UPDATE must be that same id. Any other id returns HTTP 403 and that row
  is byte-for-byte unchanged.
- Sync of a new ``source_url``: allowed for an authenticated user. The
  stored ``metadata.user_id`` must be the token ``sub``, not the body's
  ``user_id``.
- Sync of a ``source_url`` already stored for the caller: allowed. The
  stored ``metadata.user_id`` stays the token ``sub``.
- Sync of a ``source_url`` stored for someone else: HTTP 403, and that row
  stays byte-for-byte unchanged. Matching the body's ``user_id`` to the
  owner does not authorize it.
- Delete and re-analyze: admin role only. A non-admin receives HTTP 403
  even for ids they own, and no form row is deleted and no ``job_queue``
  row is inserted. An admin's delete or re-analyze affects only the ids in
  that request.

CORS
----
Responses and preflights for these routes must not grant cross-origin
access to an arbitrary website. ``Access-Control-Allow-Origin`` must not
be ``*`` and must not echo ``https://evil.example``.

Routes outside this contract (left untested here): ``/api/analyze/*``,
``/api/batch/*``, ``/health``, ``/version``, ``/status``.
"""

import base64
import hashlib
import hmac
import json
import time

import pytest

from app.services.storage import get_db, get_form, save_form

URL_A = "https://files.example/private/user-a-signed-packet.pdf"
URL_B = "https://files.example/private/user-b-tax-form.pdf"
USER_A = "extension-user-a"
USER_B = "extension-user-b"
ADMIN = "extension-admin"
EVIL_ORIGIN = "https://evil.example"
SECRET_ENV = "SECRET_KEY"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def encode_jwt(claims, secret, alg="HS256"):
    """Compact JWT. ``alg='none'`` produces an unsigned token."""
    header = {"alg": alg, "typ": "JWT"}
    signing_input = ".".join(
        [
            _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8")),
        ]
    )
    if alg == "none":
        return signing_input + "."
    if alg != "HS256":
        raise ValueError(alg)
    signature = hmac.new(
        secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return signing_input + "." + _b64url(signature)


def token(sub, role, *, secret=None, exp_offset=3600, alg="HS256", claims=None):
    import os

    if claims is None:
        claims = {
            "sub": sub,
            "role": role,
            "exp": int(time.time()) + exp_offset,
        }
    if secret is None:
        secret = os.environ[SECRET_ENV]
    return encode_jwt(claims, secret, alg=alg)


def auth(sub, role, **kwargs):
    return {"Authorization": "Bearer " + token(sub, role, **kwargs)}


def _rows(sql, params=()):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def forms_snapshot():
    return _rows(
        """
        SELECT id, url, url_hash, title, entity_name, complexity, status,
               metadata, analyzed_at, updated_at
        FROM forms
        ORDER BY id
        """
    )


def jobs_snapshot():
    return _rows(
        """
        SELECT job_id, url, url_hash, status, priority
        FROM job_queue
        ORDER BY id
        """
    )


@pytest.fixture
def seeded():
    save_form(
        URL_A,
        "User A Packet",
        "Entity A",
        2,
        {
            "user_id": USER_A,
            "nigo_score": 4,
            "form_purpose": "original-a",
            "signature_required": True,
        },
    )
    save_form(
        URL_B,
        "User B Tax Form",
        "Entity B",
        5,
        {
            "user_id": USER_B,
            "nigo_score": 7,
            "form_purpose": "original-b",
        },
    )
    stored_a = get_form(URL_A)
    stored_b = get_form(URL_B)
    assert stored_a["id"] == 1
    assert stored_b["id"] == 2
    return {"a": stored_a, "b": stored_b}


def _assert_unchanged(before_forms, before_jobs):
    assert forms_snapshot() == before_forms
    assert jobs_snapshot() == before_jobs


def _assert_body_hides_urls(response):
    body = response.get_data(as_text=True)
    assert URL_A not in body
    assert URL_B not in body


def _deny_unauthenticated(response, before_forms, before_jobs):
    assert response.status_code == 401, response.get_data(as_text=True)
    _assert_body_hides_urls(response)
    _assert_unchanged(before_forms, before_jobs)


def _deny_forbidden(response, before_forms, before_jobs):
    assert response.status_code == 403, response.get_data(as_text=True)
    _assert_body_hides_urls(response)
    _assert_unchanged(before_forms, before_jobs)


# ---------------------------------------------------------------------------
# Unauthenticated callers. No Authorization header, no cookies.
# ---------------------------------------------------------------------------


def test_unauthenticated_list_does_not_return_team_forms(client, seeded):
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    response = client.get("/api/team/forms?limit=100000")
    _deny_unauthenticated(response, before_forms, before_jobs)


def test_unauthenticated_records_unique_does_not_return_team_forms(client, seeded):
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    response = client.get("/api/records_unique?limit=100000")
    _deny_unauthenticated(response, before_forms, before_jobs)


@pytest.mark.parametrize("analysis_ids", [[1, 2], ["1", "2"]])
def test_unauthenticated_delete_does_not_remove_forms(client, seeded, analysis_ids):
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    response = client.post("/api/team/delete", json={"analysis_ids": analysis_ids})
    _deny_unauthenticated(response, before_forms, before_jobs)


def test_unauthenticated_update_does_not_rewrite_title_entity_or_metadata(client, seeded):
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    response = client.post(
        "/api/team/update",
        json={
            "user_id": "attacker",
            "analysis_id": seeded["b"]["id"],
            "form_data": {
                "form_name": "Attacker Title",
                "entity_name": "Attacker Entity",
                "complexity_score": 99,
                "nigo_score": 99,
                "form_purpose": "tampered",
            },
        },
    )
    _deny_unauthenticated(response, before_forms, before_jobs)


def test_unauthenticated_sync_does_not_overwrite_existing_source_url(client, seeded):
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    response = client.post(
        "/api/team/sync",
        json={
            "user_id": "attacker",
            "form_data": {
                "source_url": URL_A,
                "form_name": "Overwritten Packet",
                "entity_name": "Attacker Entity",
                "complexity_score": 9,
                "nigo_score": 1,
            },
        },
    )
    _deny_unauthenticated(response, before_forms, before_jobs)


def test_unauthenticated_sync_does_not_insert_a_new_form(client, seeded):
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    response = client.post(
        "/api/team/sync",
        json={
            "user_id": "attacker",
            "form_data": {
                "source_url": "https://files.example/private/attacker-new.pdf",
                "form_name": "Inserted By Attacker",
                "entity_name": "Attacker Entity",
                "complexity_score": 1,
            },
        },
    )
    _deny_unauthenticated(response, before_forms, before_jobs)
    assert get_form("https://files.example/private/attacker-new.pdf") is None


def test_unauthenticated_reanalyze_does_not_queue_jobs(client, seeded):
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    response = client.post(
        "/api/team/reanalyze",
        json={"analysis_ids": [seeded["a"]["id"], seeded["b"]["id"]]},
    )
    _deny_unauthenticated(response, before_forms, before_jobs)


def test_client_supplied_user_id_is_not_authentication(client, seeded):
    """A user id in the body or a header does not authenticate the caller."""
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    response = client.post(
        "/api/team/delete",
        json={"analysis_ids": [1, 2], "user_id": ADMIN},
        headers={"X-User-Id": ADMIN},
    )
    _deny_unauthenticated(response, before_forms, before_jobs)


# ---------------------------------------------------------------------------
# Tokens that must not authenticate.
# ---------------------------------------------------------------------------


def test_bad_signature_cannot_delete(client, seeded):
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    response = client.post(
        "/api/team/delete",
        json={"analysis_ids": [1, 2]},
        headers=auth(ADMIN, "admin", secret="test-only-placeholder-secret-wrong"),
    )
    _deny_unauthenticated(response, before_forms, before_jobs)


def test_expired_token_cannot_delete(client, seeded):
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    response = client.post(
        "/api/team/delete",
        json={"analysis_ids": [1, 2]},
        headers=auth(ADMIN, "admin", exp_offset=-3600),
    )
    _deny_unauthenticated(response, before_forms, before_jobs)


def test_alg_none_token_cannot_delete(client, seeded):
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    crafted = token(ADMIN, "admin", alg="none")
    response = client.post(
        "/api/team/delete",
        json={"analysis_ids": [1, 2]},
        headers={"Authorization": "Bearer " + crafted},
    )
    _deny_unauthenticated(response, before_forms, before_jobs)


def test_token_without_subject_cannot_delete(client, seeded):
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    crafted = token(
        None,
        None,
        claims={"role": "admin", "exp": int(time.time()) + 3600},
    )
    response = client.post(
        "/api/team/delete",
        json={"analysis_ids": [1, 2]},
        headers={"Authorization": "Bearer " + crafted},
    )
    _deny_unauthenticated(response, before_forms, before_jobs)


def test_token_without_role_cannot_delete(client, seeded):
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    crafted = token(
        None,
        None,
        claims={"sub": ADMIN, "exp": int(time.time()) + 3600},
    )
    response = client.post(
        "/api/team/delete",
        json={"analysis_ids": [1, 2]},
        headers={"Authorization": "Bearer " + crafted},
    )
    _deny_unauthenticated(response, before_forms, before_jobs)


# ---------------------------------------------------------------------------
# Authenticated, but the object acted on is not the object authorized.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role_sub", [(USER_A, "user"), (ADMIN, "admin")])
def test_caller_cannot_update_another_users_form(client, seeded, role_sub):
    sub, role = role_sub
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    response = client.post(
        "/api/team/update",
        json={
            "user_id": USER_B,
            "analysis_id": str(seeded["b"]["id"]),
            "form_data": {
                "form_name": "Stolen Title",
                "entity_name": "Stolen Entity",
                "nigo_score": 0,
                "form_purpose": "tampered",
            },
        },
        headers=auth(sub, role),
    )
    _deny_forbidden(response, before_forms, before_jobs)


def test_caller_cannot_sync_over_another_users_source_url(client, seeded):
    """Body user_id matches the owner; the token subject does not."""
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    response = client.post(
        "/api/team/sync",
        json={
            "user_id": USER_B,
            "form_data": {
                "source_url": URL_B,
                "form_name": "Replaced By User A",
                "entity_name": "Entity A",
                "complexity_score": 1,
            },
        },
        headers=auth(USER_A, "user"),
    )
    _deny_forbidden(response, before_forms, before_jobs)


def test_sync_persists_token_subject_not_body_user_id(client, seeded):
    new_url = "https://files.example/private/user-a-second.pdf"
    response = client.post(
        "/api/team/sync",
        json={
            "user_id": USER_B,
            "form_data": {
                "source_url": new_url,
                "form_name": "Second Packet",
                "entity_name": "Entity A",
                "complexity_score": 3,
            },
        },
        headers=auth(USER_A, "user"),
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    stored = get_form(new_url)
    assert stored is not None
    metadata = json.loads(stored["metadata"])
    assert metadata["user_id"] == USER_A
    untouched = get_form(URL_B)
    assert untouched["title"] == "User B Tax Form"
    assert json.loads(untouched["metadata"])["user_id"] == USER_B


def test_user_can_update_only_the_authorized_row(client, seeded):
    response = client.post(
        "/api/team/update",
        json={
            "user_id": "attacker",
            "analysis_id": seeded["a"]["id"],
            "form_data": {
                "form_name": "Renamed Packet",
                "entity_name": "Renamed Entity",
                "complexity_score": 8,
                "nigo_score": 11,
                "form_purpose": "updated-a",
            },
        },
        headers=auth(USER_A, "user"),
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    updated = get_form(URL_A)
    assert updated["id"] == seeded["a"]["id"]
    assert updated["title"] == "Renamed Packet"
    assert updated["entity_name"] == "Renamed Entity"
    assert updated["complexity"] == 8
    metadata = json.loads(updated["metadata"])
    assert metadata["user_id"] == USER_A
    assert metadata["nigo_score"] == 11
    assert metadata["form_purpose"] == "updated-a"
    other = get_form(URL_B)
    assert other["title"] == "User B Tax Form"
    assert other["entity_name"] == "Entity B"
    assert json.loads(other["metadata"])["user_id"] == USER_B
    assert json.loads(other["metadata"])["nigo_score"] == 7


def test_user_can_sync_own_source_url_without_adopting_body_user_id(client, seeded):
    response = client.post(
        "/api/team/sync",
        json={
            "user_id": "attacker",
            "form_data": {
                "source_url": URL_A,
                "form_name": "Packet Revision",
                "entity_name": "Entity A Revised",
                "complexity_score": 4,
            },
        },
        headers=auth(USER_A, "user"),
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    stored = get_form(URL_A)
    assert stored["id"] == seeded["a"]["id"]
    assert stored["title"] == "Packet Revision"
    assert stored["entity_name"] == "Entity A Revised"
    assert json.loads(stored["metadata"])["user_id"] == USER_A
    other = get_form(URL_B)
    assert other["title"] == "User B Tax Form"
    assert json.loads(other["metadata"])["user_id"] == USER_B


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/team/delete", {"analysis_ids": [1]}),
        ("/api/team/delete", {"analysis_ids": [1, 2]}),
        ("/api/team/delete", {"analysis_ids": ["1", "2"]}),
        ("/api/team/reanalyze", {"analysis_ids": [1]}),
        ("/api/team/reanalyze", {"analysis_ids": [1, 2]}),
    ],
)
def test_non_admin_cannot_delete_or_reanalyze(client, seeded, path, payload):
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    response = client.post(path, json=payload, headers=auth(USER_A, "user"))
    _deny_forbidden(response, before_forms, before_jobs)


def test_admin_delete_removes_only_the_requested_id(client, seeded):
    response = client.post(
        "/api/team/delete",
        json={"analysis_ids": [seeded["b"]["id"]]},
        headers=auth(ADMIN, "admin"),
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert get_form(URL_B) is None
    remaining = get_form(URL_A)
    assert remaining is not None
    assert remaining["id"] == seeded["a"]["id"]
    assert remaining["title"] == "User A Packet"
    assert json.loads(remaining["metadata"])["user_id"] == USER_A
    assert jobs_snapshot() == []


def test_admin_reanalyze_queues_only_the_requested_url(client, seeded):
    response = client.post(
        "/api/team/reanalyze",
        json={"analysis_ids": [str(seeded["a"]["id"])]},
        headers=auth(ADMIN, "admin"),
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    jobs = jobs_snapshot()
    assert [job["url"] for job in jobs] == [URL_A]
    assert jobs[0]["priority"] == 10
    assert get_form(URL_A)["title"] == "User A Packet"
    assert get_form(URL_B)["title"] == "User B Tax Form"


def test_authenticated_user_can_list_team_forms(client, seeded):
    before_forms, before_jobs = forms_snapshot(), jobs_snapshot()
    response = client.get(
        "/api/team/forms?limit=100000",
        headers=auth(USER_A, "user"),
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    urls = {form["source_url"] for form in payload["forms"]}
    owners = {form["user_id"] for form in payload["forms"]}
    assert urls == {URL_A, URL_B}
    assert owners == {USER_A, USER_B}
    _assert_unchanged(before_forms, before_jobs)


def test_authenticated_records_unique_lists_the_same_rows(client, seeded):
    response = client.get("/api/records_unique?limit=100000", headers=auth(USER_A, "user"))
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    urls = {form["source_url"] for form in payload["forms"]}
    assert urls == {URL_A, URL_B}


# ---------------------------------------------------------------------------
# CORS. flask-cors 4.0.0 echoes the request Origin when origins is "*".
# ---------------------------------------------------------------------------


def _assert_origin_not_granted(response):
    allowed = response.headers.get("Access-Control-Allow-Origin")
    assert allowed not in ("*", EVIL_ORIGIN), response.headers


def test_arbitrary_web_origin_is_not_allowed_on_team_routes(client, seeded):
    origin = {"Origin": EVIL_ORIGIN}
    list_response = client.get("/api/team/forms?limit=100000", headers=origin)
    _assert_origin_not_granted(list_response)
    _assert_body_hides_urls(list_response)

    authed = client.get(
        "/api/team/forms?limit=100000",
        headers={**origin, **auth(USER_A, "user")},
    )
    _assert_origin_not_granted(authed)

    preflight = client.options(
        "/api/team/delete",
        headers={
            **origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )
    _assert_origin_not_granted(preflight)
