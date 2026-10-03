#!/usr/bin/env python
"""Load the starter pack into Postgres.

Idempotent: re-running updates rows in place rather than duplicating them.
Reads corpus/corpus_manifest.csv, corpus/text/*.txt and
data/sample_addresses.csv straight from the repo.

    uv run python scripts/seed.py
    uv run python scripts/seed.py --only addresses
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.db.models import Address, Base, Document  # noqa: E402


def _int_or_none(value: str | None) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


async def seed_documents() -> tuple[int, int]:
    """Manifest rows + the plain-text bodies that exist on disk."""
    created = updated = 0
    with settings.corpus_manifest.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    async with SessionLocal() as session:
        for row in rows:
            doc_id = (row.get("doc_id") or "").strip()
            if not doc_id:
                continue

            body = None
            text_file = (row.get("text_file") or "").strip()
            if text_file:
                path = settings.corpus_dir / text_file
                if path.exists():
                    body = path.read_text(encoding="utf-8", errors="replace")

            doc = await session.get(Document, doc_id)
            if doc is None:
                doc = Document(doc_id=doc_id)
                session.add(doc)
                created += 1
            else:
                updated += 1

            doc.jurisdictions = (row.get("jurisdictions") or "").strip()
            doc.url = (row.get("url") or "").strip()
            doc.source_type = (row.get("source_type") or "").strip() or None
            doc.capture = (row.get("capture") or "").strip() or None
            doc.retrieved_at = (row.get("retrieved_at") or "").strip() or None
            doc.sha256 = (row.get("sha256") or "").strip() or None
            doc.text_file = text_file or None
            doc.status = (row.get("status") or "").strip() or None
            doc.body = body

        await session.commit()
    return created, updated


async def seed_addresses() -> tuple[int, int]:
    created = updated = 0
    with settings.addresses_csv.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    async with SessionLocal() as session:
        for row in rows:
            address_id = (row.get("address_id") or "").strip()
            if not address_id:
                continue

            address = await session.get(Address, address_id)
            if address is None:
                address = Address(address_id=address_id)
                session.add(address)
                created += 1
            else:
                updated += 1

            address.street_address = (row.get("street_address") or "").strip()
            address.postal_city = (row.get("postal_city") or "").strip()
            address.state = (row.get("state") or "").strip().upper()
            address.zip = (row.get("zip") or "").strip() or None
            address.year_built = _int_or_none(row.get("year_built"))
            address.units = _int_or_none(row.get("units"))
            address.use_code = (row.get("use_code") or "").strip() or None
            address.use_description = (row.get("use_description") or "").strip() or None
            address.source_dataset = (row.get("source_dataset") or "").strip() or None
            address.retrieved_at = (row.get("retrieved_at") or "").strip() or None

        await session.commit()
    return created, updated


async def report() -> None:
    async with SessionLocal() as session:
        docs = (await session.execute(select(func.count()).select_from(Document))).scalar_one()
        with_text = (
            await session.execute(
                select(func.count()).select_from(Document).where(Document.body.isnot(None))
            )
        ).scalar_one()
        addresses = (await session.execute(select(func.count()).select_from(Address))).scalar_one()
        no_year = (
            await session.execute(
                select(func.count()).select_from(Address).where(Address.year_built.is_(None))
            )
        ).scalar_one()
        no_units = (
            await session.execute(
                select(func.count()).select_from(Address).where(Address.units.is_(None))
            )
        ).scalar_one()

    print(f"  documents          {docs} ({with_text} with supplied text)")
    print(f"  addresses          {addresses}")
    print(f"    missing year_built  {no_year}")
    print(f"    missing units       {no_units}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=["corpus", "addresses"], default=None)
    parser.add_argument(
        "--create-tables",
        action="store_true",
        help="Create tables directly instead of running alembic (local dev only).",
    )
    args = parser.parse_args()

    if args.create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("tables created")

    if args.only != "addresses":
        created, updated = await seed_documents()
        print(f"corpus:    {created} created, {updated} updated")
    if args.only != "corpus":
        created, updated = await seed_addresses()
        print(f"addresses: {created} created, {updated} updated")

    print("\nnow in the database:")
    await report()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
