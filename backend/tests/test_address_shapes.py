"""Query variants and address shapes must preserve address identity."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from app.modules.address_lookup.adapters.address_files import read_in_from_csv
from app.modules.address_lookup.address_resolution.address_parser import parse_address
from app.modules.address_lookup.address_resolution.census import CachedGeocoder
from app.modules.address_lookup.address_resolution.models import (
    AddressInput,
    GeocodeCandidate,
    GeocodeResponse,
)
from app.modules.address_lookup.address_resolution.ports import CacheMissError
from app.modules.address_lookup.address_resolution.resolver import resolve_addresses


def candidate(
    street: str,
    city: str = "San Francisco",
    geoid: str = "0667000",
    state: str = "CA",
    zip_code: str = "94110",
) -> GeocodeCandidate:
    return GeocodeCandidate(
        matched_address=f"{street}, {city.upper()}, {state}",
        street_address=street,
        state=state,
        county=city,
        city=city,
        city_geoid=geoid,
        longitude=-122.4,
        latitude=37.7,
        benchmark="Public_AR_Current",
        vintage="Current_Current",
        zip=zip_code,
    )


class FakeGeocoder:
    def __init__(self, matches: dict[str, tuple[GeocodeCandidate, ...]]) -> None:
        self.matches = matches
        self.queries: list[str] = []

    def lookup(self, query):
        self.queries.append(query.street)
        return GeocodeResponse(self.matches.get(query.street, ()))


@pytest.mark.parametrize(
    ("original", "variant"),
    [
        ("397 05TH AV", "397 5TH AV"),
        ("5164 03RD ST", "5164 3RD ST"),
        ("1845 08TH AV", "1845 8TH AV"),
        ("173 07TH ST", "173 7TH ST"),
        ("125 03RD AV", "125 3RD AV"),
        ("1801 08TH AV", "1801 8TH AV"),
    ],
)
def test_padded_ordinal_is_retried_and_checked_against_original(
    original: str, variant: str
) -> None:
    geocoder = FakeGeocoder({variant: (candidate(variant),)})
    result = resolve_addresses([AddressInput("A1", original, "San Francisco", "CA")], geocoder)[0]

    assert result.status == "resolved"
    assert result.input.street_address == original
    assert geocoder.queries == [original, variant]
    assert result.attempts[-1].query_variant == "zero_padded_ordinal"
    assert result.accepted_endpoints[0].input_street == original
    assert result.accepted_endpoints[0].query_variant == "zero_padded_ordinal"
    assert "zero_padded_ordinal_query_used" in result.warnings


def test_variant_never_drops_direction_or_rewrites_house_number() -> None:
    geocoder = FakeGeocoder({"397 N 5TH AV": (candidate("397 5TH AV"),)})
    directional = resolve_addresses(
        [AddressInput("A1", "397 N 05TH AV", "San Francisco", "CA")], geocoder
    )[0]
    assert directional.status == "needs_review"
    assert geocoder.queries == ["397 N 05TH AV", "397 N 5TH AV"]

    house_number = FakeGeocoder({})
    resolve_addresses([AddressInput("A2", "0397 GUERRERO ST", "San Francisco", "CA")], house_number)
    assert house_number.queries == ["0397 GUERRERO ST"]


def test_earlier_place_ambiguity_is_not_cleared_by_a_zip_query() -> None:
    first = candidate("12 OAK ST", "Hoboken", "3432250", "NJ", "07102")
    second = candidate("12 OAK ST", "Jersey City", "3436000", "NJ", "07102")

    class ZipGeocoder:
        def lookup(self, query):
            return GeocodeResponse((first,) if query.zip else (first, second))

    result = resolve_addresses(
        [AddressInput("A1", "12 OAK ST", "Hoboken", "NJ", "07102")], ZipGeocoder()
    )[0]
    assert result.status == "needs_review"
    assert result.legal_city is None
    assert any(attempt.outcome == "ambiguous" for attempt in result.attempts)


@pytest.mark.parametrize(
    ("raw", "kind", "components"),
    [
        ("21 GUERRERO ST", "single", ("21 GUERRERO ST",)),
        ("1031-1035 CLINTON ST", "same_street_range", ("1031 CLINTON ST", "1035 CLINTON ST")),
        ("322-322.5 Western Ave", "fractional_range", ("322 Western Ave", "322.5 Western Ave")),
        ("38-38- SOMME ST", "same_street_range", ("38 SOMME ST",)),
        (
            "238 & 242 GARFIELD AVE",
            "compound_same_street",
            ("238 GARFIELD AVE", "242 GARFIELD AVE"),
        ),
        ("600 JACKSON/601 HARRISON", "compound_cross_street", ("600 JACKSON", "601 HARRISON")),
        ("Harvard ST LOT 2A-13", "no_house_number", ("Harvard ST LOT 2A-13",)),
        ("12 & 14", "unsupported", ()),
    ],
)
def test_address_shape_parsing(raw: str, kind: str, components: tuple[str, ...]) -> None:
    shape = parse_address(raw)
    assert shape.raw_value == raw
    assert shape.kind == kind
    assert shape.components == components


def test_fractional_range_rejects_dropped_house_number() -> None:
    geocoder = FakeGeocoder(
        {
            "322 Western Ave": (
                candidate("322 WESTERN AVE", "Cambridge", "2511000", "MA", "02139"),
            ),
            "322.5 Western Ave": (
                candidate("5 WESTERN AVE", "Cambridge", "2511000", "MA", "02139"),
            ),
        }
    )
    result = resolve_addresses(
        [AddressInput("A1", "322-322.5 Western Ave", "Cambridge", "MA")], geocoder
    )[0]
    assert result.address_shape.kind == "fractional_range"
    assert result.status == "needs_review"
    assert len(result.accepted_endpoints) == 1


def test_compound_components_require_complete_evidence() -> None:
    matches = {
        "238 GARFIELD AVE": (
            candidate("238 GARFIELD AVE", "Jersey City", "3436000", "NJ", "07305"),
        ),
        "242 GARFIELD AVE": (
            candidate("242 GARFIELD AVE", "Jersey City", "3436000", "NJ", "07305"),
        ),
    }
    address = AddressInput("A1", "238 & 242 GARFIELD AVE", "Jersey City", "NJ", "07304")
    complete = resolve_addresses([address], FakeGeocoder(matches))[0]
    assert complete.status == "resolved"
    assert len(complete.accepted_endpoints) == 2
    assert "compound_address_checked_at_all_components" in complete.warnings

    incomplete = resolve_addresses(
        [address], FakeGeocoder({"242 GARFIELD AVE": matches["242 GARFIELD AVE"]})
    )[0]
    assert incomplete.status == "needs_review"
    assert len(incomplete.accepted_endpoints) == 1


def test_cross_street_compound_remains_reviewable_even_when_both_match() -> None:
    geocoder = FakeGeocoder(
        {
            "600 JACKSON": (candidate("600 JACKSON", "Hoboken", "3432250", "NJ", "07030"),),
            "601 HARRISON": (candidate("601 HARRISON", "Hoboken", "3432250", "NJ", "07030"),),
        }
    )
    result = resolve_addresses(
        [AddressInput("A1", "600 JACKSON/601 HARRISON", "Hoboken", "NJ")], geocoder
    )[0]
    assert result.status == "needs_review"
    assert len(result.accepted_endpoints) == 2
    assert "compound_address_requires_source_review" in result.warnings


def test_uncached_variant_remains_an_explicit_offline_failure() -> None:
    class PartialCache:
        def lookup(self, query):
            if query.street == "397 05TH AV":
                return GeocodeResponse(())
            raise CacheMissError("Variant not cached")

    result = resolve_addresses(
        [AddressInput("A1", "397 05TH AV", "San Francisco", "CA")], PartialCache()
    )[0]
    assert result.status == "needs_review"
    assert [attempt.outcome for attempt in result.attempts] == ["no_match", "cache_miss"]


def test_sample_cache_replays_new_resolutions_without_changing_review_policy() -> None:
    root = Path(__file__).resolve().parents[2]
    addresses = read_in_from_csv(root / "data/sample_addresses.csv")
    results = resolve_addresses(addresses, CachedGeocoder(root / "data/census_geocode_cache.jsonl"))
    by_id = {result.address_id: result for result in results}

    assert len(results) == 500
    assert Counter(result.status for result in results) == {
        "resolved": 474,
        "needs_review": 26,
    }
    assert all(attempt.outcome != "cache_miss" for result in results for attempt in result.attempts)
    for address_id in ("A0115", "A0229", "A0328", "A0357", "A0364", "A0484"):
        assert by_id[address_id].legal_city == "San Francisco"
        assert "zero_padded_ordinal_query_used" in by_id[address_id].warnings
    assert by_id["A0344"].legal_city == "Jersey City"
    assert len(by_id["A0344"].accepted_endpoints) == 2
    assert by_id["A0168"].status == "needs_review"
