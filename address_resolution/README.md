# Address Resolver

This module turns raw address records into reliable, legally useful location information.

Its main entry point is: `resolve_addresses(addresses, geocoder)`
It takes a sequence of `AddressInput` records and returns one `ResolvedAddress` for each input, preserving the original order.



## How it works

Address resolution is split into two parts:
1. **Geocoding** – finding possible matches for an address using an external service.
2. **Resolution** – deciding which candidate, if any, should be accepted.

The geocoder is passed into `resolve_addresses()` instead of being hard-coded because geocoding is an external I/O operation.

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
python3 -m address_resolution.run
```

The command produces:

- `data/resolved_addresses.json` – one processed record per input, including unresolved records marked `needs_review`
- `data/census_geocode_cache.jsonl` – cached Census API responses
- `data/address_review.csv` – addresses that could not be resolved confidently and require manual review

After a successful online run, the resolver can also run entirely from the cache:

```bash
python3 -m address_resolution.run --offline
```

If an address is not available in the cache while running offline, it is added to the review file instead of guessing a result.

## Postal city vs. legal city

An important distinction is made between:

- `postal_city` – the city name written in the input CSV or mailing address
- `legal_city` – the incorporated municipality the property legally belongs to

These are not always the same.

Neighborhood names and postal aliases can help the geocoder find an address, but they are **not** treated as authoritative legal jurisdictions.

A `legal_city` is only assigned when the address can be matched to a validated Census **Incorporated Place**.

For address ranges, both endpoints must resolve to the same incorporated place before that place is accepted as the legal city.

## Confidence and edge cases

Census geocoding coordinates are estimates based on street ranges. They are not exact property-boundary determinations.

Addresses that are ambiguous or close to municipal boundaries should therefore be verified using a second authoritative public source.

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

The file-based reader and writer functions currently live in `io.py`.

They can later be replaced with database-backed implementations without changing the core `resolve_addresses()` logic.
