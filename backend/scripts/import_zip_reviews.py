#!/usr/bin/env python
"""Import reviewed ZIP decisions from CSV into the append-only audit table."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.core.db import SessionLocal, engine  # noqa: E402
from app.db.models import Address, AddressZipReview  # noqa: E402
from app.modules.address_lookup.adapters.zip_reviews import read_zip_reviews_csv  # noqa: E402
from app.modules.address_lookup.address_resolution.models import ZipAssessment  # noqa: E402
from app.modules.address_lookup.service import input_from_db  # noqa: E402
from app.modules.address_lookup.zip_reviews import validate_decision  # noqa: E402


async def import_reviews(path: Path) -> tuple[int, int]:
    decisions = read_zip_reviews_csv(path)
    if len({decision.address_id for decision in decisions}) != len(decisions):
        raise ValueError("ZIP review CSV contains duplicate address IDs")
    async with SessionLocal() as session:
        addresses = (
            (
                await session.execute(
                    select(Address)
                    .where(Address.address_id.in_([decision.address_id for decision in decisions]))
                    .options(selectinload(Address.jurisdiction))
                )
            )
            .scalars()
            .all()
        )
        by_id = {address.address_id: address for address in addresses}
        for decision in decisions:
            address = by_id.get(decision.address_id)
            if address is None:
                raise ValueError(f"Unknown address ID: {decision.address_id}")
            evidence = address.jurisdiction.resolution_evidence if address.jurisdiction else None
            assessment_data = (evidence or {}).get("zip_assessment")
            assessment = ZipAssessment(**assessment_data) if assessment_data else None
            validate_decision(decision, input_from_db(address), assessment)

        created = skipped = 0
        for decision in decisions:
            existing = (
                await session.execute(
                    select(AddressZipReview).where(
                        AddressZipReview.address_id == decision.address_id,
                        AddressZipReview.input_street_address == decision.input_street_address,
                        AddressZipReview.input_postal_city == decision.input_postal_city,
                        AddressZipReview.input_state == decision.input_state,
                        AddressZipReview.input_zip == decision.input_zip,
                        AddressZipReview.decision == decision.decision,
                        AddressZipReview.confirmed_zip == decision.confirmed_zip,
                        AddressZipReview.source_url == decision.source_url,
                        AddressZipReview.reason == decision.reason,
                        AddressZipReview.reviewer == decision.reviewer,
                        AddressZipReview.reviewed_at == decision.reviewed_at,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                skipped += 1
                continue
            session.add(AddressZipReview(**asdict(decision)))
            created += 1
        await session.commit()
    return created, skipped


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    try:
        created, skipped = await import_reviews(args.input)
        print(f"Imported {created} ZIP reviews; skipped {skipped} duplicates")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
