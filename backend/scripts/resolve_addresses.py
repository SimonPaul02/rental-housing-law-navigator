"""CLI wiring for the CSV/JSON hackathon workflow."""

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.address_lookup.adapters.address_files import (  # noqa: E402
    read_in_from_csv,
    read_review_overrides_csv,
    write_review_csv,
    write_to_internal_json,
    write_zip_review_csv,
)
from app.modules.address_lookup.address_resolution.census import (  # noqa: E402
    CachedGeocoder,
    CensusGeocoder,
)
from app.modules.address_lookup.address_resolution.resolver import (  # noqa: E402
    apply_review_overrides,
    resolve_addresses,
)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve sample addresses to legal cities")
    parser.add_argument("--input", default=str(DATA_DIR / "sample_addresses.csv"))
    parser.add_argument("--output", default=str(DATA_DIR / "resolved_addresses.json"))
    parser.add_argument("--cache", default=str(DATA_DIR / "census_geocode_cache.jsonl"))
    parser.add_argument("--review", default=str(DATA_DIR / "address_review.csv"))
    parser.add_argument("--zip-review", default=str(DATA_DIR / "zip_review.csv"))
    parser.add_argument("--overrides", help="CSV of source-backed review decisions")
    parser.add_argument("--offline", action="store_true", help="Only use cached Census responses")
    args = parser.parse_args()

    addresses = read_in_from_csv(args.input)
    geocoder = CachedGeocoder(args.cache, None if args.offline else CensusGeocoder())
    results = resolve_addresses(addresses, geocoder)
    if args.overrides:
        results = apply_review_overrides(results, read_review_overrides_csv(args.overrides))
    write_to_internal_json(results, args.output)
    write_review_csv(results, args.review)
    write_zip_review_csv(results, args.zip_review)
    counts = Counter(item.status for item in results)
    print(f"Processed {len(results)} addresses: {dict(counts)}")
    print(f"Resolution output: {args.output}")
    print(f"Review queue: {args.review}")
    print(f"ZIP review: {args.zip_review}")
    failed_lookups = [
        result
        for result in results
        if result.status != "resolved"
        and any(attempt.outcome in {"cache_miss", "service_error"} for attempt in result.attempts)
    ]
    if failed_lookups:
        print(
            f"Error: {len(failed_lookups)} addresses remain unresolved after lookup failures.",
            file=sys.stderr,
        )
        return 2
    if addresses and not counts["resolved"]:
        print(
            "Error: no addresses resolved; check the review queue and geocoder response.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
