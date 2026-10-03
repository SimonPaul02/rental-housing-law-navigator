"""WorkOS is the identity provider: it owns every account, credential and session.

There are no organisations here, deliberately. Nobody shares a workspace with anybody else,
so there would be nothing for an organisation to scope. That has one consequence worth
stating plainly, because it shapes everything below: without an organisation membership an
AuthKit access token carries no ``role`` claim, and WorkOS has no other primitive that would
put one there. So the role a person signed up as - renter, housing provider, housing agency,
housing advocate - cannot come from the token. It lives in our own ``users`` table, keyed by
the WorkOS user id, and is set through ``/api/accounts/me``.

What the token is for, then, is identity and nothing else: its ``sub`` names the caller, and
that is the only thing here that is ever trusted. Verifying the signature is the whole job,
and that needs only WorkOS's published keys - so this backend holds no WorkOS secret.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic

import httpx
import jwt
from fastapi import Depends, HTTPException, Request

from app.core.config import Settings, settings

log = logging.getLogger("rhln.auth")

# Keys and issuer are both scoped to the client id, which is what stops a token minted for
# some other WorkOS application from being accepted here.
JWKS_URL = "https://api.workos.com/sso/jwks/{client_id}"
ISSUER = "https://api.workos.com/user_management/{client_id}"

# Signing keys rotate rarely, so caching them keeps the network out of the request path. A
# key id we have never seen means a rotation and is fetched sooner than the TTL, so a
# rotation is picked up in seconds; the floor stops a stream of invented key ids from
# becoming a stream of requests to WorkOS.
KEY_TTL_SECONDS = 3600
REFETCH_FLOOR_SECONDS = 10


@dataclass(frozen=True)
class Principal:
    """The caller, exactly as WorkOS describes them - no more."""

    user_id: str
    session_id: str | None = None


class Auth:
    def __init__(self, config: Settings):
        self.client_id = config.workos_client_id
        self.environment = config.environment
        self._client: httpx.AsyncClient | None = None
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.client_id)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def key(self, kid: str) -> jwt.PyJWK:
        age = monotonic() - self._fetched
        if age > KEY_TTL_SECONDS or (kid not in self._keys and age > REFETCH_FLOOR_SECONDS):
            response = await self.client.get(JWKS_URL.format(client_id=self.client_id))
            response.raise_for_status()
            keys: dict[str, jwt.PyJWK] = {}
            for entry in response.json().get("keys", []):
                try:
                    keys[entry["kid"]] = jwt.PyJWK(entry)
                except Exception:  # noqa: BLE001 - a key of a kind nothing here is signed
                    continue  # with; the rest of the set still works
            self._keys = keys
            self._fetched = monotonic()
        if kid not in self._keys:
            raise HTTPException(401, "Session expired. Please sign in again.")
        return self._keys[kid]

    async def principal(self, token: str) -> Principal:
        try:
            kid = jwt.get_unverified_header(token).get("kid", "")
        except jwt.InvalidTokenError:
            raise HTTPException(401, "Please sign in.") from None
        try:
            key = await self.key(kid)
        except httpx.HTTPError:
            # An unreachable key server is our outage, not a rejected session. Saying
            # "expired" here would send someone through a sign-in that cannot help them.
            raise HTTPException(503, "Sign-in service unreachable.") from None
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=ISSUER.format(client_id=self.client_id),
                # AuthKit access tokens carry no `aud`; the issuer check above is what binds
                # a token to this environment.
                options={"verify_aud": False, "require": ["exp", "iss", "sub"]},
            )
        except jwt.InvalidTokenError:
            raise HTTPException(401, "Session expired. Please sign in again.") from None
        return Principal(user_id=claims["sub"], session_id=claims.get("sid"))


auth = Auth(settings)


async def optional_principal(request: Request) -> Principal | None:
    """Authentication for the module routers.

    A checkout with no WorkOS environment has nobody to ask and runs open - that is what the
    tests and `make dev-api` use. Production never does, not even unconfigured: a missing
    client id turns everyone away instead of publishing the data. `/api/health` says which
    mode is in force, so the frontend never has to guess.
    """
    if not auth.configured:
        if settings.environment == "production":
            raise HTTPException(401, "Please sign in.")
        return None
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "Please sign in.")
    return await auth.principal(header[7:])


async def require_principal(
    principal: Principal | None = Depends(optional_principal),
) -> Principal:
    """Authentication for anything that reads or writes one person's own records.

    This one has no open mode. Without WorkOS there is no person to be, and guessing an
    identity so the endpoint can answer would be inventing a tenant - every row these
    endpoints touch belongs to exactly one account.
    """
    if principal is None:
        raise HTTPException(503, "Sign-in is not configured in this environment.")
    return principal
