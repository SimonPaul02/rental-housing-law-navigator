#!/usr/bin/env python
"""Create, list or delete a test account in WorkOS.

Signing up through AuthKit needs a mailbox that receives the verification code, which a
made-up test address never will. This creates the user directly with the address already
marked verified, so the sign-in card works on the first try.

There are no organisations in this deployment, so there is nothing to join and no role to
assign here: the role is chosen in the app, on first sign-in, and stored in our own `users`
table. Deleting a user here therefore leaves that row behind - the API drops it the moment
nobody can sign in as them, which is never, so use `/api/accounts/me` (DELETE) from the app
if you want it gone too.

    scripts/workos_user.py test@rhln-local.dev
    scripts/workos_user.py test@rhln-local.dev --password 'Something-Chosen-1'
    scripts/workos_user.py --list
    scripts/workos_user.py test@rhln-local.dev --delete

The API key comes from WORKOS_API_KEY or from frontend/.env.local. Avoid a domain that is
verified on a WorkOS organisation (example.com, say) or WorkOS requires SSO for it and a
password will not work.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = "https://api.workos.com"


def api_key() -> str:
    key = os.getenv("WORKOS_API_KEY", "")
    if not key:
        env = ROOT / "frontend" / ".env.local"
        if env.exists():
            found = re.search(r"^WORKOS_API_KEY=(.+)$", env.read_text(), re.M)
            key = found.group(1).strip().strip("\"'") if found else ""
    if not key:
        sys.exit("WORKOS_API_KEY is not set (environment or frontend/.env.local).")
    return key


def call(key: str, path: str, body: dict | None = None, method: str | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        API + path,
        data=data,
        method=method or ("POST" if body else "GET"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        sys.exit(f"WorkOS {error.code}: {error.read().decode()[:400]}")


def find(key: str, email: str) -> dict | None:
    query = urllib.parse.quote(email)
    hits = call(key, f"/user_management/users?email={query}").get("data", [])
    return hits[0] if hits else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email", nargs="?", help="omit with --list")
    parser.add_argument("--password", default="", help="default: a random one, printed")
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--list", action="store_true", dest="listing")
    args = parser.parse_args()

    key = api_key()

    if args.listing:
        for user in call(key, "/user_management/users?limit=100").get("data", []):
            verified = "verified" if user.get("email_verified") else "unverified"
            print(f"{user['id']}  {user['email']}  ({verified})")
        return

    if not args.email:
        parser.error("an email address is required unless --list is given")

    existing = find(key, args.email)

    if args.delete:
        if not existing:
            sys.exit(f"{args.email} does not exist.")
        call(key, f"/user_management/users/{existing['id']}", method="DELETE")
        print(f"deleted {args.email} ({existing['id']})")
        return

    if existing:
        print(f"{args.email} already exists ({existing['id']}) - password unchanged")
        return

    password = args.password or f"Rhln-{secrets.token_urlsafe(12)}-7"
    user = call(
        key,
        "/user_management/users",
        # Without this, AuthKit asks for a code from an email that a made-up test address
        # never receives, and the account cannot be signed into at all.
        {"email": args.email, "password": password, "email_verified": True},
    )
    print(f"created {args.email} ({user['id']})")
    print(f"password {password}")
    print("Sign in at http://localhost:3000 and pick a role.")


if __name__ == "__main__":
    main()
