# Address Resolver

This module turns raw address records into reliable, legally useful location information.

Its main entry point is: `resolve_addresses(addresses, geocoder)`
It takes a sequence of `AddressInput` records and returns one `ResolvedAddress` for each input, preserving the original order.



## How it works

Address resolution is split into two parts:
1. **Geocoding** – finding possible matches for an address using an external service.
2. **Resolution** – deciding which candidate, if any, should be accepted.

Candidate validation happens in `_credible()` and `_lookup_endpoint()`. The final decision lives in:

```python
choose_resolution()
```

This function is intentionally pure: it looks at prepared endpoint results and decides whether to accept a city or request review. This makes the final decision easy to test without making real Census API requests.

The common `Geocoder` interface is defined in `ports.py`.

The Census API client and the local cache are implementations of this interface and can therefore be replaced without changing the resolver itself.

## Running the resolver

To resolve the addresses from the provided CSV using the Census geocoder:

```bash
cd backend && python3 scripts/resolve_addresses.py
```

The command produces:

- `data/resolved_addresses.json` – one processed record per input, including unresolved records marked `needs_review`
- `data/census_geocode_cache.jsonl` – cached Census API responses
- `data/address_review.csv` – unresolved addresses and unexpected postal/legal-city mismatches to inspect
- `data/zip_review.csv` – ZIP discrepancies, with the original and accepted Census ZIPs

The command exits with code `2` if a cache miss or service error leaves an address
unresolved, or if no addresses resolve at all. It still writes the output and
review files so the failure can be inspected. Ordinary unmatched or incomplete
addresses remain in the review file without making a partly successful run fail.

After a successful online run, the resolver can also run entirely from the cache:

```bash
cd backend && python3 scripts/resolve_addresses.py --offline
```

If an address is not available in the cache while running offline, it is added to the review file instead of guessing a result, and the command exits with code `2`.

After checking a review case against a public source, copy
`data/review_overrides.example.csv` to a new CSV and supply it with `--overrides`:

```bash
cd backend && python3 scripts/resolve_addresses.py --offline --overrides ../data/address_overrides.csv
```

Each override needs an `address_id`, the original `input_street_address`,
`input_postal_city`, and `input_state`, the verified legal state and city, a
public `source_url`, a reason, reviewer, and review date. These original address
fields are checked to avoid applying an old decision to a changed input. The
output records `resolution_method=review_override` and
preserves the review evidence. `legal_county` and `city_geoid` are optional.

## Postal city vs. legal city

An important distinction is made between:

- `postal_city` – the city name written in the input CSV or mailing address
- `legal_city` – the incorporated municipality the property legally belongs to

These are not always the same.

Neighborhood names and postal aliases can help the geocoder find an address, but they are **not** treated as authoritative legal jurisdictions.

A `legal_city` is assigned from a validated Census **Incorporated Place** or a documented review override. If the Census place differs from the mailing city, the result keeps the Census place, adds a warning, and appears in the review CSV.

For address ranges, both endpoints must resolve to the same incorporated place before that place is accepted as the legal city.

The resolver accepts a small set of equivalent street spellings (for example,
`SECOND`/`2ND`, `WY`/`WAY`, and `SOUTH`/`S`) while keeping the house number and
every directional component. These results carry a `street_normalized_match`
warning. Missing or changed directions, street-name typos, and unmatched range
endpoints remain in the review queue; they need source-backed verification.

## ZIP evidence

ZIP quality is assessed separately from legal jurisdiction. `zip_assessment.py`
compares the original ZIP with the ZIP of every accepted endpoint; rejected
Census candidates are excluded. Its statuses are `missing_input`,
`invalid_for_state`, `insufficient_evidence`, `matches_all`,
`matches_some_endpoints`, and `mismatch`. The original ZIP and legal city are
never changed by this assessment.

The offline snapshot currently has 467 resolved jurisdictions and 33 needing
review. It has 92 ZIP discrepancies on resolved addresses. `zip_review.csv`
contains 93 rows because one more address has an invalid ZIP and an unresolved
jurisdiction. The detailed assessment and endpoint evidence are included in
`resolved_addresses.json` and stored by the backend in
`address_jurisdictions.resolution_evidence`.

After migrating an existing database, backfill ZIP evidence from the checked-in
Census cache without changing jurisdiction decisions:

```bash
cd backend && python3 scripts/backfill_zip_assessments.py
```

Human ZIP findings use the separate, append-only `address_zip_reviews` table, so
resolver reruns cannot erase them. An operator can copy
`data/zip_reviews.example.csv`, add source-backed decisions, and import them:

```bash
cd backend && python3 scripts/import_zip_reviews.py --input ../data/zip_reviews.csv
```

The import checks that the reviewed street, mailing city, state, and original
ZIP still match the address. `GET /api/address-lookup/zip-review` lists current
discrepancies; `GET /api/address-lookup/addresses/{address_id}` includes the
latest reviewed decision and whether it still matches the input.
The CSV `decision` is one of `input_zip_supported`, `census_zip_supported`,
`another_zip_supported`, or `inconclusive`; a supported decision needs a
five-digit `confirmed_zip`. Imports are operator-run because the app has no
restricted reviewer role.

## Confidence and edge cases

Census geocoding coordinates are estimates based on street ranges. They are not exact property-boundary determinations.

Addresses that are ambiguous or close to municipal boundaries should therefore be verified using a second authoritative public source. The current code does not detect boundary proximity automatically.

The resolver prefers returning an unresolved/review result over inventing a legal city when the available evidence is insufficient.

## Output and downstream use

`resolved_addresses.json` is an internal intermediate representation. It is **not** the challenge's final `lookups.json` format.

Later modules can join the resolved addresses with extracted legal rules using `address_id`.

For example:

```text
Raw address
    ↓
Address Resolver
    ↓
ResolvedAddress
    ├── Census matched address
    ├── coordinates
    ├── postal city
    └── legal city
    ↓
Legal rule evaluation
```

The file-based reader and writer functions live in `adapters/address_files.py`.

They can later be replaced with database-backed implementations without changing the core `resolve_addresses()` logic.
