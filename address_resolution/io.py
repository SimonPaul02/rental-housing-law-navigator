"""File adapters. The resolver imports none of these functions."""

from __future__ import annotations

import csv
from dataclasses import asdict
import json
from pathlib import Path

from .models import AddressInput, ResolvedAddress


_REQUIRED_COLUMNS = {"address_id", "street_address", "postal_city", "state", "zip"}


def read_in_from_csv(path: str | Path) -> list[AddressInput]:
    """Read the supplied assessor CSV into storage-independent input records."""
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        missing = _REQUIRED_COLUMNS - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Missing address columns: {', '.join(sorted(missing))}")
        rows = [
            AddressInput(
                address_id=(row["address_id"] or "").strip(),
                street_address=(row["street_address"] or "").strip(),
                postal_city=(row["postal_city"] or "").strip(),
                state=(row["state"] or "").strip().upper(),
                zip=(row["zip"] or "").strip(),
                year_built=(row.get("year_built") or "").strip(),
                units=(row.get("units") or "").strip(),
                use_code=(row.get("use_code") or "").strip(),
                use_description=(row.get("use_description") or "").strip(),
                source_dataset=(row.get("source_dataset") or "").strip(),
                retrieved_at=(row.get("retrieved_at") or "").strip(),
            )
            for row in reader
        ]
    return rows


def write_to_internal_json(results: list[ResolvedAddress], path: str | Path) -> None:
    """Write a list of processed data points, ready to replace with a DB sink."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump([asdict(item) for item in results], stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(destination)


def write_review_csv(results: list[ResolvedAddress], path: str | Path) -> None:
    """Write only unresolved addresses for evidence-based review."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "address_id", "street_address", "postal_city", "state", "zip",
                "warnings", "attempts", "candidate_cities",
            ],
        )
        writer.writeheader()
        for item in results:
            if item.status == "resolved":
                continue
            writer.writerow(
                {
                    "address_id": item.address_id,
                    "street_address": item.input.street_address,
                    "postal_city": item.input.postal_city,
                    "state": item.input.state,
                    "zip": item.input.zip,
                    "warnings": "; ".join(item.warnings),
                    "attempts": "; ".join(
                        f"{attempt.outcome}: {attempt.query.street}, {attempt.query.city}"
                        for attempt in item.attempts
                    ),
                    "candidate_cities": "; ".join(
                        sorted({item.city for item in item.candidates if item.city})
                    ),
                }
            )
