"""Resolve addresses without knowing how input or output is stored."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from urllib.parse import urlparse

from .address_parser import parse_address
from .models import (
    AcceptedEndpoint,
    AddressInput,
    AddressQuery,
    AddressShape,
    GeocodeCandidate,
    LookupAttempt,
    ResolvedAddress,
    ReviewOverride,
)
from .ports import CacheMissError, Geocoder, GeocoderError
from .zip_assessment import assess_zip, zip_is_plausible

# These names are search hints only. The returned incorporated place is the
# evidence for a legal city; an alias never becomes a resolution by itself.
DEFAULT_SEARCH_ALIASES: dict[str, str] = {
    "allston": "Boston",
    "brighton": "Boston",
    "dorchester": "Boston",
    "east boston": "Boston",
    "hyde park": "Boston",
    "jamaica plain": "Boston",
    "mattapan": "Boston",
    "roxbury": "Boston",
    "south boston": "Boston",
    "san ysidro": "San Diego",
    "van nuys": "Los Angeles",
}

_HOUSE_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?[A-Za-z]?)\s+(.+)$")
_PADDED_STREET_ORDINAL = re.compile(
    r"^(\s*\d+(?:\.\d+)?[A-Za-z]?\s+(?:(?:N|S|E|W|NORTH|SOUTH|EAST|WEST)\s+)?)"
    r"0+(\d+)(ST|ND|RD|TH)(\b.*)$",
    re.I,
)
_SUFFIXES = {
    "AV": "AVE",
    "AVENUE": "AVE",
    "BL": "BLVD",
    "BOULEVARD": "BLVD",
    "DRIVE": "DR",
    "LANE": "LN",
    "PLACE": "PL",
    "ROAD": "RD",
    "STREET": "ST",
    "TERRACE": "TER",
    "WAY": "WAY",
}
_DIRECTIONS = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}
_ORDINALS = {
    "FIRST": "1ST",
    "SECOND": "2ND",
    "THIRD": "3RD",
    "FOURTH": "4TH",
    "FIFTH": "5TH",
    "SIXTH": "6TH",
    "SEVENTH": "7TH",
    "EIGHTH": "8TH",
    "NINTH": "9TH",
    "TENTH": "10TH",
}
_EXTRA_SUFFIXES = {"BLV": "BLVD", "WY": "WAY"}


def _house_number_and_street(value: str) -> tuple[str | None, tuple[str, ...]]:
    match = _HOUSE_NUMBER.match(value.split(",", 1)[0])
    if not match:
        return None, ()
    number_match = re.fullmatch(r"(\d+)(\.\d+)?([A-Za-z]?)", match.group(1))
    assert number_match is not None
    number = (
        f"{int(number_match.group(1))}{number_match.group(2) or ''}{number_match.group(3).upper()}"
    )
    words = re.findall(r"[A-Z0-9]+", match.group(2).upper())
    words = [re.sub(r"^0+(\d+)(ST|ND|RD|TH)$", r"\1\2", word) for word in words]
    return number, tuple(_SUFFIXES.get(word, word) for word in words)


def _canonical_street(words: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize only address spellings that preserve every street component."""
    normalized = list(words)
    if len(normalized) >= 3 and normalized[-2] in {"APT", "UNIT"}:
        normalized = normalized[:-2]
    if normalized and normalized[0] in _DIRECTIONS:
        normalized[0] = _DIRECTIONS[normalized[0]]
    ordinal_index = 1 if normalized and normalized[0] in {"N", "S", "E", "W"} else 0
    if len(normalized) > ordinal_index:
        normalized[ordinal_index] = _ORDINALS.get(
            normalized[ordinal_index], normalized[ordinal_index]
        )
    if normalized[:4] == ["M", "L", "KING", "JR"]:
        normalized[:4] = ["MARTIN", "LUTHER", "KING", "JR"]
    if normalized:
        normalized[-1] = _EXTRA_SUFFIXES.get(normalized[-1], normalized[-1])
    return tuple(normalized)


def _street_match_kind(query: AddressQuery, candidate: GeocodeCandidate) -> str | None:
    expected_number, expected_street = _house_number_and_street(query.street)
    matched_number, matched_street = _house_number_and_street(candidate.street_address)
    if expected_number is None or expected_number != matched_number or not expected_street:
        return None
    if expected_street == matched_street:
        return "exact"
    if _canonical_street(expected_street) == _canonical_street(matched_street):
        return "normalized"
    return None


def describe_candidate_differences(
    address: AddressInput, candidate: GeocodeCandidate, shape: AddressShape | None = None
) -> tuple[str, ...]:
    """Summarize observable input/candidate differences for a human reviewer.

    These are comparisons, not a claim that the source or candidate is correct.
    Multi-address inputs are compared against all parsed components.
    """
    components = (shape or parse_address(address.street_address)).components
    parsed = [_house_number_and_street(component) for component in components]
    source_numbers = tuple(dict.fromkeys(number for number, _ in parsed if number))
    source_streets = tuple(dict.fromkeys(words for _, words in parsed if words))
    matched_number, matched_street = _house_number_and_street(candidate.street_address)
    differences: list[str] = []

    if source_numbers and matched_number not in source_numbers:
        differences.append(
            f"house number: {' / '.join(source_numbers)} → {matched_number or 'missing'}"
        )
    if source_streets and not any(
        _canonical_street(words) == _canonical_street(matched_street)
        for words in source_streets
    ):
        differences.append(
            f"street: {' / '.join(' '.join(words) for words in source_streets)}"
            f" → {' '.join(matched_street) or 'missing'}"
        )
    if candidate.state.upper() != address.state.upper():
        differences.append(f"state: {address.state.upper()} → {candidate.state.upper()}")
    if address.zip and candidate.zip and address.zip != candidate.zip:
        differences.append(f"input ZIP: {address.zip} → candidate ZIP: {candidate.zip}")
    if candidate.city and candidate.city.casefold() != address.postal_city.casefold():
        differences.append(
            f"postal city: {address.postal_city} → incorporated place: {candidate.city}"
        )
    if not candidate.city or not candidate.city_geoid:
        differences.append("incorporated place unverified")
    return tuple(differences)


def _unpadded_ordinal_query(street: str) -> str | None:
    """Offer one reversible search spelling; never rewrite a house number."""
    match = _PADDED_STREET_ORDINAL.fullmatch(street)
    if not match:
        return None
    prefix, digits, suffix, remainder = match.groups()
    number = int(digits)
    if 10 < number % 100 < 14:
        expected_suffix = "TH"
    else:
        expected_suffix = {1: "ST", 2: "ND", 3: "RD"}.get(number % 10, "TH")
    if suffix.upper() != expected_suffix:
        return None
    return f"{prefix}{number}{suffix}{remainder}"


def _queries(
    street: str,
    address: AddressInput,
    aliases: Mapping[str, str],
) -> tuple[AddressQuery, ...]:
    cities = [address.postal_city.strip()]
    alias = aliases.get(address.postal_city.strip().casefold())
    if alias and alias.casefold() != cities[0].casefold():
        cities.append(alias)
    queries: list[AddressQuery] = []
    for city in cities:
        if not city:
            continue
        # Omit ZIP first: several supplied ZIPs conflict with their city.
        queries.append(AddressQuery(street, city, address.state.upper()))
        if address.zip and zip_is_plausible(address.zip, address.state):
            queries.append(AddressQuery(street, city, address.state.upper(), address.zip))
    return tuple(dict.fromkeys(queries))


def _credible(candidate: GeocodeCandidate, query: AddressQuery) -> bool:
    return (
        bool(candidate.city and candidate.city_geoid)
        and candidate.state.upper() == query.state.upper()
        and _street_match_kind(query, candidate) is not None
    )


def _lookup_endpoint(
    street: str,
    address: AddressInput,
    geocoder: Geocoder,
    aliases: Mapping[str, str],
) -> tuple[GeocodeCandidate | None, list[LookupAttempt], list[GeocodeCandidate]]:
    attempts: list[LookupAttempt] = []
    candidates_seen: list[GeocodeCandidate] = []
    if _house_number_and_street(street)[0] is None:
        query = AddressQuery(street, address.postal_city, address.state.upper())
        return (
            None,
            [LookupAttempt(query, "invalid_input", "Missing or unsupported house number")],
            [],
        )
    query_groups = [("original", _queries(street, address, aliases))]
    variant_street = _unpadded_ordinal_query(street)
    if variant_street and variant_street != street:
        query_groups.append(("zero_padded_ordinal", _queries(variant_street, address, aliases)))
    earlier_ambiguity = False
    for variant, queries in query_groups:
        for query in queries:
            source_query = replace(query, street=street)
            try:
                response = geocoder.lookup(query)
            except CacheMissError as exc:
                attempts.append(LookupAttempt(query, "cache_miss", str(exc), variant))
                continue
            except GeocoderError as exc:
                attempts.append(LookupAttempt(query, "service_error", str(exc), variant))
                continue
            candidates_seen.extend(response.candidates)
            credible = [item for item in response.candidates if _credible(item, source_query)]
            places = {item.city_geoid for item in credible}
            if len(places) == 1:
                selected = next(
                    (
                        item
                        for item in credible
                        if _street_match_kind(source_query, item) == "exact"
                    ),
                    credible[0],
                )
                if earlier_ambiguity:
                    attempts.append(
                        LookupAttempt(
                            query,
                            "ambiguous",
                            "An earlier query returned multiple incorporated places",
                            variant,
                        )
                    )
                    continue
                kind = _street_match_kind(source_query, selected)
                attempts.append(
                    LookupAttempt(
                        query,
                        "match" if kind == "exact" else "normalized_match",
                        selected.matched_address,
                        variant,
                    )
                )
                return selected, attempts, candidates_seen
            if len(places) > 1:
                earlier_ambiguity = True
                attempts.append(
                    LookupAttempt(query, "ambiguous", "Multiple incorporated places", variant)
                )
            elif response.candidates:
                attempts.append(
                    LookupAttempt(
                        query, "rejected", "No candidate passed address and place checks", variant
                    )
                )
            else:
                attempts.append(LookupAttempt(query, "no_match", query_variant=variant))
    return None, attempts, candidates_seen


def choose_resolution(
    address: AddressInput,
    endpoint_results: Sequence[GeocodeCandidate | None],
    attempts: Sequence[LookupAttempt],
    candidates: Sequence[GeocodeCandidate],
    search_aliases: Mapping[str, str] = DEFAULT_SEARCH_ALIASES,
    accepted_endpoints: Sequence[AcceptedEndpoint] = (),
    expected_endpoint_count: int | None = None,
    address_shape: AddressShape | None = None,
) -> ResolvedAddress:
    """Make the final decision from supplied evidence; performs no I/O."""
    warnings: list[str] = []
    shape = address_shape or parse_address(address.street_address)
    endpoint_count = (
        expected_endpoint_count if expected_endpoint_count is not None else len(endpoint_results)
    )
    zip_assessment = assess_zip(address, accepted_endpoints, endpoint_count)
    if zip_assessment.status == "missing_input":
        warnings.append("missing_input_zip")
    elif zip_assessment.status == "invalid_for_state":
        warnings.append("suspicious_input_zip")
    if shape.kind in {"same_street_range", "fractional_range"} and len(endpoint_results) > 1:
        warnings.append("house_number_range_checked_at_both_endpoints")
    elif shape.kind == "compound_same_street":
        warnings.append("compound_address_checked_at_all_components")
    elif shape.kind == "compound_cross_street":
        warnings.append("compound_address_requires_source_review")
    elif shape.kind == "no_house_number":
        warnings.append("missing_house_number")
    elif shape.kind == "unsupported":
        warnings.append("unsupported_address_shape")
    if any(attempt.outcome == "normalized_match" for attempt in attempts):
        warnings.append("street_normalized_match")
    if any(
        attempt.query_variant == "zero_padded_ordinal"
        and attempt.outcome in {"match", "normalized_match"}
        for attempt in attempts
    ):
        warnings.append("zero_padded_ordinal_query_used")

    selected: GeocodeCandidate | None = None
    if endpoint_results and all(item is not None for item in endpoint_results):
        resolved_candidates = [item for item in endpoint_results if item is not None]
        if len({item.city_geoid for item in resolved_candidates}) == 1:
            selected = resolved_candidates[0]
        else:
            warnings.append("range_endpoints_disagree")
    if selected is not None and shape.kind == "compound_cross_street":
        selected = None
    if selected is not None:
        expected_city = search_aliases.get(
            address.postal_city.strip().casefold(), address.postal_city.strip()
        )
        if expected_city and selected.city and selected.city.casefold() != expected_city.casefold():
            warnings.append("postal_city_differs_from_legal_city")
        if zip_assessment.status in {
            "invalid_for_state",
            "mismatch",
            "matches_some_endpoints",
        } and (
            zip_assessment.status == "matches_some_endpoints"
            or (selected.zip and address.zip != selected.zip)
        ):
            warnings.append("input_zip_differs_from_match")
    if selected is None:
        warnings.append("jurisdiction_needs_review")

    return ResolvedAddress(
        address_id=address.address_id,
        input=address,
        status="resolved" if selected else "needs_review",
        legal_state=selected.state if selected else None,
        legal_county=selected.county if selected else None,
        legal_city=selected.city if selected else None,
        city_geoid=selected.city_geoid if selected else None,
        longitude=selected.longitude if selected else None,
        latitude=selected.latitude if selected else None,
        matched_address=selected.matched_address if selected else None,
        benchmark=selected.benchmark if selected else None,
        vintage=selected.vintage if selected else None,
        resolution_method="geocoder" if selected else "unresolved",
        address_shape=shape,
        zip_assessment=zip_assessment,
        accepted_endpoints=tuple(accepted_endpoints),
        warnings=tuple(warnings),
        attempts=tuple(attempts),
        candidates=tuple(dict.fromkeys(candidates)),
    )


def resolve_addresses(
    addresses: Sequence[AddressInput],
    geocoder: Geocoder,
    *,
    search_aliases: Mapping[str, str] = DEFAULT_SEARCH_ALIASES,
) -> list[ResolvedAddress]:
    """Return one processed data point per input, in the original order.

    The geocoder is injected so this method is independent of CSV, JSON and DB
    storage. Use a cached or fake geocoder for reproducible offline runs.
    """
    seen_ids: set[str] = set()
    output: list[ResolvedAddress] = []
    for address in addresses:
        if not address.address_id or address.address_id in seen_ids:
            raise ValueError(f"Missing or duplicate address_id: {address.address_id!r}")
        seen_ids.add(address.address_id)
        endpoint_results: list[GeocodeCandidate | None] = []
        accepted_endpoints: list[AcceptedEndpoint] = []
        attempts: list[LookupAttempt] = []
        candidates: list[GeocodeCandidate] = []
        shape = parse_address(address.street_address)
        streets = shape.components
        if not streets:
            attempts.append(
                LookupAttempt(
                    AddressQuery(
                        address.street_address, address.postal_city, address.state.upper()
                    ),
                    "invalid_input",
                    shape.note or "Unsupported address shape",
                )
            )
        for street in streets:
            chosen, endpoint_attempts, endpoint_candidates = _lookup_endpoint(
                street, address, geocoder, search_aliases
            )
            endpoint_results.append(chosen)
            if chosen is not None:
                accepted_endpoints.append(
                    AcceptedEndpoint(
                        input_street=street,
                        candidate=chosen,
                        match_kind="normalized"
                        if endpoint_attempts[-1].outcome == "normalized_match"
                        else "exact",
                        query_variant=endpoint_attempts[-1].query_variant,
                    )
                )
            attempts.extend(endpoint_attempts)
            candidates.extend(endpoint_candidates)
        output.append(
            choose_resolution(
                address,
                endpoint_results,
                attempts,
                candidates,
                search_aliases,
                accepted_endpoints,
                len(streets) or 1,
                shape,
            )
        )
    return output


def apply_review_overrides(
    results: Sequence[ResolvedAddress], overrides: Sequence[ReviewOverride]
) -> list[ResolvedAddress]:
    """Apply source-backed human decisions while preserving one result per input."""
    by_id: dict[str, ReviewOverride] = {}
    for override in overrides:
        if override.address_id in by_id:
            raise ValueError(f"Duplicate review override: {override.address_id}")
        if not all(
            (
                override.input_postal_city,
                override.input_state,
                override.legal_state,
                override.legal_city,
                override.reason,
                override.reviewer,
                override.reviewed_at,
            )
        ):
            raise ValueError(f"Incomplete review override: {override.address_id}")
        source = urlparse(override.source_url)
        if source.scheme not in {"http", "https"} or not source.netloc:
            raise ValueError(f"Review override needs a public source URL: {override.address_id}")
        by_id[override.address_id] = override

    unknown_ids = set(by_id) - {result.address_id for result in results}
    if unknown_ids:
        raise ValueError(f"Review overrides contain unknown address IDs: {sorted(unknown_ids)}")

    output: list[ResolvedAddress] = []
    for result in results:
        override = by_id.get(result.address_id)
        if override is None:
            output.append(result)
            continue
        original_street = " ".join(result.input.street_address.split()).casefold()
        reviewed_street = " ".join(override.input_street_address.split()).casefold()
        if original_street != reviewed_street:
            raise ValueError(f"Review override has stale street address: {result.address_id}")
        if (
            override.input_postal_city.casefold() != result.input.postal_city.strip().casefold()
            or override.input_state.upper() != result.input.state.upper()
        ):
            raise ValueError(f"Review override has stale city or state: {result.address_id}")
        warnings = [
            warning for warning in result.warnings if warning != "jurisdiction_needs_review"
        ]
        warnings.append("review_override_applied")
        if override.legal_state.upper() != result.input.state.upper():
            warnings.append("reviewed_state_differs_from_input")
        output.append(
            replace(
                result,
                status="resolved",
                legal_state=override.legal_state.upper(),
                legal_county=override.legal_county,
                legal_city=override.legal_city,
                city_geoid=override.city_geoid,
                longitude=None,
                latitude=None,
                matched_address=None,
                benchmark=None,
                vintage=None,
                resolution_method="review_override",
                review_override=override,
                warnings=tuple(dict.fromkeys(warnings)),
            )
        )
    return output
