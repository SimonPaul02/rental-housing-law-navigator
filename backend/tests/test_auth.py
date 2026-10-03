"""WorkOS token verification, and the two modes the API runs in.

No network and no database: the signing key is generated here and WorkOS's key endpoint is
answered by a stub, so these assert what the verifier accepts rather than that WorkOS is up.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app.core import auth as auth_module
from app.core.auth import Auth, Principal, optional_principal, require_principal
from app.modules.accounts.schemas import ROLE_LABELS, Role

CLIENT_ID = "client_test123"
ISSUER = f"https://api.workos.com/user_management/{CLIENT_ID}"
KID = "sso_oidc_key_pair_test"


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks(key: rsa.RSAPrivateKey) -> dict:
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {"keys": [{**public, "kid": KID, "alg": "RS256", "use": "sig"}]}


def token_for(key: rsa.RSAPrivateKey, **overrides) -> str:
    now = dt.datetime.now(tz=dt.UTC)
    claims = {
        "sub": "user_01ABCDEF",
        "sid": "session_01ABCDEF",
        "iss": ISSUER,
        "iat": now,
        "exp": now + dt.timedelta(minutes=5),
        **overrides,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})


class Settings:
    """Only the two fields Auth reads, so these tests do not depend on the real env file."""

    def __init__(self, client_id: str = CLIENT_ID, environment: str = "development"):
        self.workos_client_id = client_id
        self.environment = environment


def verifier(jwks: dict, *, status: int = 200, fail: bool = False) -> Auth:
    """An Auth whose key fetches are answered locally."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if fail:
            raise httpx.ConnectError("key server unreachable", request=request)
        return httpx.Response(status, json=jwks)

    instance = Auth(Settings())
    instance._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    instance.calls = calls  # type: ignore[attr-defined]
    return instance


# ------------------------------------------------------------- verification ---
async def test_valid_token_yields_the_subject(key, jwks):
    principal = await verifier(jwks).principal(token_for(key))
    assert principal == Principal(user_id="user_01ABCDEF", session_id="session_01ABCDEF")


async def test_a_token_from_another_workos_application_is_refused(key, jwks):
    """The issuer is scoped to the client id, which is the whole point of checking it."""
    other = token_for(key, iss="https://api.workos.com/user_management/client_someoneelse")
    with pytest.raises(HTTPException) as caught:
        await verifier(jwks).principal(other)
    assert caught.value.status_code == 401


async def test_an_expired_token_is_refused(key, jwks):
    stale = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=1)
    with pytest.raises(HTTPException) as caught:
        await verifier(jwks).principal(token_for(key, exp=stale))
    assert caught.value.status_code == 401


async def test_a_token_signed_by_the_wrong_key_is_refused(jwks):
    impostor = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException) as caught:
        await verifier(jwks).principal(token_for(impostor))
    assert caught.value.status_code == 401


async def test_an_unsigned_token_is_refused(jwks):
    """`alg: none` is the classic way in. PyJWT is told RS256 and nothing else."""
    forged = jwt.encode({"sub": "user_x", "iss": ISSUER}, key="", algorithm="none")
    with pytest.raises(HTTPException) as caught:
        await verifier(jwks).principal(forged)
    assert caught.value.status_code == 401


async def test_garbage_is_refused_without_fetching_keys(jwks):
    instance = verifier(jwks)
    with pytest.raises(HTTPException) as caught:
        await instance.principal("not-a-token")
    assert caught.value.status_code == 401
    assert instance.calls == []  # type: ignore[attr-defined]


async def test_an_unreachable_key_server_is_our_outage_not_a_dead_session(key, jwks):
    """503, not 401: sending somebody through a sign-in that cannot work is worse than
    telling them the service is down."""
    with pytest.raises(HTTPException) as caught:
        await verifier(jwks, fail=True).principal(token_for(key))
    assert caught.value.status_code == 503


async def test_keys_are_fetched_once_and_reused(key, jwks):
    instance = verifier(jwks)
    await instance.principal(token_for(key))
    await instance.principal(token_for(key))
    assert len(instance.calls) == 1  # type: ignore[attr-defined]


async def test_an_unknown_key_id_does_not_become_a_stream_of_requests(key, jwks):
    """A rotation is refetched early; invented key ids are rate-limited by the floor."""
    instance = verifier(jwks)
    await instance.principal(token_for(key))
    for _ in range(5):
        with pytest.raises(HTTPException):
            await instance.principal(
                jwt.encode({"sub": "x"}, key, algorithm="RS256", headers={"kid": "made-up"})
            )
    assert len(instance.calls) == 1  # type: ignore[attr-defined]


# ------------------------------------------------------------------- modes ---
def request_with(header: str | None) -> httpx.Request:
    headers = {"Authorization": header} if header else {}
    return httpx.Request("GET", "http://testserver/api/rules", headers=headers)


async def test_without_workos_the_api_runs_open_outside_production(monkeypatch, jwks):
    monkeypatch.setattr(auth_module, "auth", Auth(Settings(client_id="")))
    monkeypatch.setattr(auth_module.settings, "environment", "development")
    assert await optional_principal(request_with(None)) is None


async def test_without_workos_production_refuses_everyone(monkeypatch):
    """A forgotten client id must lock the door, not leave it open."""
    monkeypatch.setattr(auth_module, "auth", Auth(Settings(client_id="")))
    monkeypatch.setattr(auth_module.settings, "environment", "production")
    with pytest.raises(HTTPException) as caught:
        await optional_principal(request_with(None))
    assert caught.value.status_code == 401


async def test_with_workos_a_missing_bearer_header_is_refused(monkeypatch, jwks):
    monkeypatch.setattr(auth_module, "auth", verifier(jwks))
    with pytest.raises(HTTPException) as caught:
        await optional_principal(request_with(None))
    assert caught.value.status_code == 401


async def test_with_workos_a_non_bearer_header_is_refused(monkeypatch, jwks):
    monkeypatch.setattr(auth_module, "auth", verifier(jwks))
    with pytest.raises(HTTPException) as caught:
        await optional_principal(request_with("Basic dXNlcjpwYXNz"))
    assert caught.value.status_code == 401


async def test_with_workos_a_bearer_token_identifies_the_caller(monkeypatch, key, jwks):
    monkeypatch.setattr(auth_module, "auth", verifier(jwks))
    principal = await optional_principal(request_with(f"Bearer {token_for(key)}"))
    assert principal is not None and principal.user_id == "user_01ABCDEF"


async def test_personal_endpoints_have_no_open_mode():
    """Accounts and saved places belong to one person, so running open would mean
    inventing whose rows to serve."""
    with pytest.raises(HTTPException) as caught:
        await require_principal(None)
    assert caught.value.status_code == 503


async def test_personal_endpoints_pass_a_real_principal_through():
    principal = Principal(user_id="user_01ABCDEF")
    assert await require_principal(principal) is principal


# ------------------------------------------------------------------- roles ---
def test_every_role_has_a_label():
    assert set(ROLE_LABELS) == set(Role)
    assert [r.value for r in Role] == ["renter", "provider", "agency", "advocate"]
