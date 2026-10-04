#!/usr/bin/env python
"""Set the D022 AB 325 date only after D092 supplies its exact evidence span."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal, engine  # noqa: E402
from app.modules.change_tracking.effective_date_review import apply_ab325_date  # noqa: E402


async def main() -> None:
    async with SessionLocal() as session:
        updated = await apply_ab325_date(session)
        await session.commit()
    print(f"AB 325 effective date verified for {len(updated)} rule(s): {updated}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
