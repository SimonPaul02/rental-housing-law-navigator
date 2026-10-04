"""Give existing accounts the buildings the deployment was loaded with.

New accounts get the imported book when they are created, in
`accounts.service.register`. This is for the ones that existed before that was
true - including every account on a deployment whose database was seeded with
addresses but never with anybody's list, which is what makes a housing
provider sign in to an empty portfolio and a page telling them to go and type
their own buildings in.

Adds nothing an account already holds, so running it twice is a no-op, and a
provider who removed a building gets it back - which is the one thing to know
before running it on a deployment people are using.

    cd backend && .venv/bin/python scripts/adopt_import.py          # every account
    cd backend && .venv/bin/python scripts/adopt_import.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.db.models import Address, SavedPlace, User  # noqa: E402
from app.modules.accounts.schemas import Role  # noqa: E402
from app.modules.accounts.service import ADOPTS_THE_IMPORT, adopt_the_import  # noqa: E402


async def main(dry_run: bool) -> int:
    async with SessionLocal() as session:
        book = (
            await session.execute(select(func.count()).select_from(Address).where(Address.imported))
        ).scalar_one()
        users = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
        print(f"{book} imported buildings, {len(users)} accounts\n")

        added = 0
        for user in users:
            held = (
                await session.execute(
                    select(func.count())
                    .select_from(SavedPlace)
                    .where(SavedPlace.owner_id == user.workos_user_id)
                )
            ).scalar_one()
            adopts = Role(user.role) in ADOPTS_THE_IMPORT
            if not adopts:
                print(f"  {user.role:9s} {user.email:30s} {held:4d} held  (keeps its own list)")
                continue
            if dry_run:
                print(f"  {user.role:9s} {user.email:30s} {held:4d} held  -> would hold {book}")
                continue
            fresh = await adopt_the_import(session, user)
            added += fresh
            print(f"  {user.role:9s} {user.email:30s} {held:4d} held  + {fresh} adopted")

        if dry_run:
            print("\n(dry run - nothing written)")
        else:
            await session.commit()
            print(f"\n{added} buildings adopted")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report, write nothing.")
    raise SystemExit(asyncio.run(main(parser.parse_args().dry_run)))
