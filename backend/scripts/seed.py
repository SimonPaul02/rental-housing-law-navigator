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
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.db.models import Address, AddressJurisdiction, Base, Document  # noqa: E402
from app.modules.address_lookup.adapters.property_facts import to_payload  # noqa: E402
from app.modules.address_lookup.property_facts import (  # noqa: E402
    PropertyInput,
    build_property_facts,
)


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
    facts_by_id = {
        facts.address_id: facts
        for facts in build_property_facts(
            [
                PropertyInput(
                    address_id=(row.get("address_id") or "").strip(),
                    year_built=row.get("year_built") or "",
                    units=row.get("units") or "",
                    use_code=row.get("use_code") or "",
                    use_description=row.get("use_description") or "",
                    source_dataset=row.get("source_dataset") or "",
                    retrieved_at=row.get("retrieved_at") or "",
                )
                for row in rows
            ]
        )
    }

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
            facts = facts_by_id[address_id]
            address.year_built = facts.year_built.value
            address.units = facts.units.value
            address.use_code = (row.get("use_code") or "").strip() or None
            address.use_description = (row.get("use_description") or "").strip() or None
            address.source_dataset = (row.get("source_dataset") or "").strip() or None
            address.retrieved_at = (row.get("retrieved_at") or "").strip() or None
            address.property_facts = to_payload(facts)

        await session.commit()
    return created, updated


async def seed_jurisdictions() -> tuple[int, int]:
    """Load the checked-in offline snapshot without replacing reviewed/live decisions."""
    path = settings.data_root / "data" / "resolved_addresses.json"
    if not path.exists():
        return 0, 0
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    created = updated = 0
    async with SessionLocal() as session:
        for result in snapshot:
            address_id = result["address_id"]
            row = await session.get(AddressJurisdiction, address_id)
            if row and row.method in {"manual", "review_override"}:
                continue
            if row and row.method == "geocoder" and row.resolution_evidence:
                continue
            if row is None:
                row = AddressJurisdiction(address_id=address_id)
                session.add(row)
                created += 1
            else:
                updated += 1
            row.legal_city = result.get("legal_city")
            row.legal_state = result.get("legal_state")
            row.county = result.get("legal_county")
            row.method = result.get("resolution_method") or "unresolved"
            row.matched_address = result.get("matched_address")
            row.latitude = result.get("latitude")
            row.longitude = result.get("longitude")
            row.place_geoid = result.get("city_geoid")
            row.confidence = None
            row.note = "; ".join(result.get("warnings") or ()) or None
            row.resolution_evidence = result
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
        juris_created, juris_updated = await seed_jurisdictions()
        print(f"jurisdictions: {juris_created} created, {juris_updated} updated")

    print("\nnow in the database:")
    await report()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
