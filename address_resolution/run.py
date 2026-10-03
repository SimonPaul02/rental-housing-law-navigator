"""CLI wiring for the CSV/JSON hackathon workflow."""

import argparse
from collections import Counter

from .census import CachedGeocoder, CensusGeocoder
from .io import read_in_from_csv, write_review_csv, write_to_internal_json
from .resolver import resolve_addresses


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve sample addresses to legal cities")
    parser.add_argument("--input", default="data/sample_addresses.csv")
    parser.add_argument("--output", default="data/resolved_addresses.json")
    parser.add_argument("--cache", default="data/census_geocode_cache.jsonl")
    parser.add_argument("--review", default="data/address_review.csv")
    parser.add_argument("--offline", action="store_true", help="Only use cached Census responses")
    args = parser.parse_args()

    addresses = read_in_from_csv(args.input)
    geocoder = CachedGeocoder(args.cache, None if args.offline else CensusGeocoder())
    results = resolve_addresses(addresses, geocoder)
    write_to_internal_json(results, args.output)
    write_review_csv(results, args.review)
    counts = Counter(item.status for item in results)
    print(f"Processed {len(results)} addresses: {dict(counts)}")
    print(f"Resolution output: {args.output}")
    print(f"Review queue: {args.review}")


if __name__ == "__main__":
    main()
