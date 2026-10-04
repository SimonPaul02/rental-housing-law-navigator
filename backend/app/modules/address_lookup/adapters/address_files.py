"""File adapters. The resolver imports none of these functions."""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from app.modules.address_lookup.address_resolution.models import (
    AddressInput,
    ResolvedAddress,
    ReviewOverride,
)
from app.modules.address_lookup.address_resolution.resolver import describe_candidate_differences
from app.modules.address_lookup.zip_reviews import ZipReviewDecision, validate_decision

_REQUIRED_COLUMNS = {"address_id", "street_address", "postal_city", "state", "zip"}
_REVIEW_COLUMNS = {
    "address_id",
    "input_street_address",
    "input_postal_city",
    "input_state",
    "legal_state",
    "legal_city",
    "source_url",
    "reason",
    "reviewer",
    "reviewed_at",
}


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


def read_review_overrides_csv(path: str | Path) -> list[ReviewOverride]:
    """Read documented human decisions without coupling the resolver to CSV."""
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        missing = _REVIEW_COLUMNS - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Missing review override columns: {', '.join(sorted(missing))}")
        return [
            ReviewOverride(
                address_id=(row["address_id"] or "").strip(),
                input_street_address=(row["input_street_address"] or "").strip(),
                input_postal_city=(row["input_postal_city"] or "").strip(),
                input_state=(row["input_state"] or "").strip().upper(),
                legal_state=(row["legal_state"] or "").strip().upper(),
                legal_city=(row["legal_city"] or "").strip(),
                source_url=(row["source_url"] or "").strip(),
                reason=(row["reason"] or "").strip(),
                reviewer=(row["reviewer"] or "").strip(),
                reviewed_at=(row["reviewed_at"] or "").strip(),
                legal_county=(row.get("legal_county") or "").strip() or None,
                city_geoid=(row.get("city_geoid") or "").strip() or None,
            )
            for row in reader
        ]


def write_review_csv(results: list[ResolvedAddress], path: str | Path) -> None:
    """Write unresolved addresses and unexpected postal/legal-city mismatches."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            lineterminator="\n",
            fieldnames=[
                "address_id",
                "status",
                "street_address",
                "postal_city",
                "legal_city",
                "state",
                "zip",
                "warnings",
                "attempts",
                "candidate_cities",
                "candidate_differences",
            ],
        )
        writer.writeheader()
        for item in results:
            if item.resolution_method == "review_override":
                continue
            if (
                item.status == "resolved"
                and "postal_city_differs_from_legal_city" not in item.warnings
            ):
                continue
            writer.writerow(
                {
                    "address_id": item.address_id,
                    "status": item.status,
                    "street_address": item.input.street_address,
                    "postal_city": item.input.postal_city,
                    "legal_city": item.legal_city or "",
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
                    "candidate_differences": _candidate_differences_for_review(item),
                }
            )


def _candidate_differences_for_review(item: ResolvedAddress) -> str:
    """Keep one readable comparison per distinct Census candidate."""
    seen: set[tuple[str, str, str | None, str | None, str | None]] = set()
    summaries: list[str] = []
    for candidate in item.candidates:
        key = (
            candidate.street_address,
            candidate.state,
            candidate.city,
            candidate.city_geoid,
            candidate.zip,
        )
        if key in seen:
            continue
        seen.add(key)
        differences = describe_candidate_differences(item.input, candidate, item.address_shape)
        place = candidate.city or "no incorporated place"
        zip_code = candidate.zip or "no ZIP"
        summary = "; ".join(differences) if differences else "address fields match"
        summaries.append(f"{candidate.street_address} [{place}, {zip_code}]: {summary}")
    return " | ".join(summaries) if summaries else "no candidate returned"


def write_zip_review_csv(
    results: list[ResolvedAddress],
    path: str | Path,
    reviews: Sequence[ZipReviewDecision] = (),
) -> None:
    """Export ZIP discrepancies and validated repair evidence without changing raw data."""
    by_id = {result.address_id: result for result in results}
    reviewed: dict[str, ZipReviewDecision] = {}
    for decision in reviews:
        if decision.address_id in reviewed:
            raise ValueError(f"Duplicate ZIP review: {decision.address_id}")
        result = by_id.get(decision.address_id)
        if result is None:
            raise ValueError(f"ZIP review has unknown address ID: {decision.address_id}")
        validate_decision(decision, result.input, result.zip_assessment)
        reviewed[decision.address_id] = decision
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            lineterminator="\n",
            fieldnames=[
                "address_id",
                "jurisdiction_status",
                "street_address",
                "postal_city",
                "legal_city",
                "state",
                "input_zip",
                "matched_zips",
                "zip_status",
                "accepted_endpoints",
                "expected_endpoints",
                "reason",
                "source_dataset",
                "source_retrieved_at",
                "review_decision",
                "confirmed_property_zip",
                "review_source_url",
                "review_reason",
                "reviewer",
                "reviewed_at",
            ],
        )
        writer.writeheader()
        for result in results:
            assessment = result.zip_assessment
            if assessment is None or assessment.status not in {
                "invalid_for_state",
                "mismatch",
                "matches_some_endpoints",
            }:
                continue
            decision = reviewed.get(result.address_id)
            writer.writerow(
                {
                    "address_id": result.address_id,
                    "jurisdiction_status": result.status,
                    "street_address": result.input.street_address,
                    "postal_city": result.input.postal_city,
                    "legal_city": result.legal_city or "",
                    "state": result.input.state,
                    "input_zip": assessment.input_zip,
                    "matched_zips": "; ".join(assessment.matched_zips),
                    "zip_status": assessment.status,
                    "accepted_endpoints": assessment.accepted_endpoints,
                    "expected_endpoints": assessment.expected_endpoints,
                    "reason": assessment.reason,
                    "source_dataset": assessment.source_dataset,
                    "source_retrieved_at": assessment.source_retrieved_at,
                    "review_decision": decision.decision if decision else "",
                    "confirmed_property_zip": decision.confirmed_zip if decision else "",
                    "review_source_url": decision.source_url if decision else "",
                    "review_reason": decision.reason if decision else "",
                    "reviewer": decision.reviewer if decision else "",
                    "reviewed_at": decision.reviewed_at.isoformat() if decision else "",
                }
            )
