# Property facts

`property_facts` turns supplied property rows into one typed, evidence-bearing
`PropertyFacts` record per `address_id`. Its public entry point is
`build_property_facts(inputs)`. The core package uses only the Python standard
library and does no file, network, geocoding, or legal-rule IO.

```python
from property_facts import PropertyInput, build_property_facts

records = build_property_facts([
    PropertyInput(
        address_id="A0005",
        use_code="7700",
        use_description="Alameda County use code (5+ units)",
        source_dataset="Alameda County parcels",
        retrieved_at="2026-10-01T22:50Z",
    )
])
assert records[0].units.value is None
```

Each fact has a semantic `kind`, typed `value`, original `raw_value`, `status`,
and `provenance` (source dataset, field, retrieval time, optional URL). Status is `present`,
`not_supplied`, `invalid`, or `conflicted`. Consumers should treat invalid and
conflicted facts as unknown for a condition that depends on them. A year built
never fills a certificate-of-occupancy date; the optional date input fields
require separately sourced, day-precision values. The default `record_scope`
is `source_address_row`; the package makes no parcel/building/unit identity claim.
When an enriched field comes from another source, supply its own `Provenance`
through `PropertyInput.field_provenance`; otherwise fields inherit the row source.

The `pipeline.property_facts_adapter` module connects the package to the
current address resolver's `AddressInput` and `ResolvedAddress` types. It is
the only place this feature imports `address_resolution`. Call either
`from_address_inputs(addresses)` or `from_resolved_addresses(results)`.
The latter uses the original `ResolvedAddress.input` even if jurisdiction
resolution needs review. It does not alter the resolver or its output files.

The core parses numbers and full ISO dates and flags obvious conflicts between
an exact unit count and an explicit numeric range in the supplied use
description. It does not decode assessor use codes, infer exact unit counts
from category descriptions, or make rule-coverage decisions.

Run checks with `python3 -m unittest discover -s tests -p 'test_property_facts.py'`.
