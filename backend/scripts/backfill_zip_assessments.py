#!/usr/bin/env python
"""Backfill ZIP evidence from cached Census responses without changing jurisdictions."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.db.models import Address  # noqa: E402
from app.modules.address_lookup.address_resolution.census import CachedGeocoder  # noqa: E402
from app.modules.address_lookup.address_resolution.resolver import resolve_addresses  # noqa: E402
from app.modules.address_lookup.service import input_from_db, merge_zip_evidence  # noqa: E402


async def main() -> None:
    cache = settings.data_root / "data" / "census_geocode_cache.jsonl"
    async with SessionLocal() as session:
        addresses = (
            (
                await session.execute(
                    select(Address)
                    .options(selectinload(Address.jurisdiction))
                    .order_by(Address.address_id)
                )
            )
            .scalars()
            .all()
        )
        results = resolve_addresses(
            [input_from_db(address) for address in addresses], CachedGeocoder(cache)
        )
        failed = [
            result.address_id
            for result in results
            if any(
                attempt.outcome in {"cache_miss", "service_error"} for attempt in result.attempts
            )
        ]
        if failed:
            raise RuntimeError(f"Offline Census cache is incomplete for {len(failed)} addresses")

        updated = 0
        for address, result in zip(addresses, results, strict=True):
            row = address.jurisdiction
            if row is None or row.resolution_evidence is None:
                continue
            if row.method == "geocoder" and row.place_geoid != result.city_geoid:
                raise RuntimeError(
                    f"Jurisdiction changed for {address.address_id}; re-resolve it first"
                )
            row.resolution_evidence = merge_zip_evidence(row.resolution_evidence, result)
            updated += 1
        await session.commit()
        print(f"Backfilled ZIP evidence for {updated} of {len(addresses)} addresses")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
