"""CSV adapter for source-backed ZIP review decisions."""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from app.modules.address_lookup.zip_reviews import ZipReviewDecision

_COLUMNS = {
    "address_id",
    "input_street_address",
    "input_postal_city",
    "input_state",
    "input_zip",
    "decision",
    "confirmed_zip",
    "source_url",
    "reason",
    "reviewer",
    "reviewed_at",
}


def read_zip_reviews_csv(path: str | Path) -> list[ZipReviewDecision]:
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        missing = _COLUMNS - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Missing ZIP review columns: {', '.join(sorted(missing))}")
        return [
            ZipReviewDecision(
                address_id=(row["address_id"] or "").strip(),
                input_street_address=(row["input_street_address"] or "").strip(),
                input_postal_city=(row["input_postal_city"] or "").strip(),
                input_state=(row["input_state"] or "").strip().upper(),
                input_zip=(row["input_zip"] or "").strip(),
                decision=(row["decision"] or "").strip(),
                confirmed_zip=(row["confirmed_zip"] or "").strip() or None,
                source_url=(row["source_url"] or "").strip(),
                reason=(row["reason"] or "").strip(),
                reviewer=(row["reviewer"] or "").strip(),
                reviewed_at=dt.date.fromisoformat((row["reviewed_at"] or "").strip()),
            )
            for row in reader
        ]
