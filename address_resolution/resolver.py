"""Resolve addresses without knowing how input or output is stored."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import re
from urllib.parse import urlparse

from .models import (
    AddressInput,
    AddressQuery,
    GeocodeCandidate,
    LookupAttempt,
    ResolvedAddress,
    ReviewOverride,
)
from .ports import CacheMissError, Geocoder, GeocoderError


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

_HOUSE_RANGE = re.compile(r"^\s*(\d+(?:\.\d+)?[A-Za-z]?)-(\d+(?:\.\d+)?[A-Za-z]?)-?\s+(.+)$")
_HOUSE_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?[A-Za-z]?)\s+(.+)$")
_SUFFIXES = {
    "AV": "AVE", "AVENUE": "AVE", "BL": "BLVD", "BOULEVARD": "BLVD",
    "DRIVE": "DR", "LANE": "LN", "PLACE": "PL", "ROAD": "RD",
    "STREET": "ST", "TERRACE": "TER", "WAY": "WAY",
}
_DIRECTIONS = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}
_ORDINALS = {
    "FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD", "FOURTH": "4TH",
    "FIFTH": "5TH", "SIXTH": "6TH", "SEVENTH": "7TH", "EIGHTH": "8TH",
    "NINTH": "9TH", "TENTH": "10TH",
}
_EXTRA_SUFFIXES = {"BLV": "BLVD", "WY": "WAY"}
_ZIP_PREFIXES = {"CA": ("9",), "NJ": ("07", "08"), "MA": ("01", "02", "055")}


def _house_number_and_street(value: str) -> tuple[str | None, tuple[str, ...]]:
    match = _HOUSE_NUMBER.match(value.split(",", 1)[0])
    if not match:
        return None, ()
    number_match = re.fullmatch(r"(\d+)(\.\d+)?([A-Za-z]?)", match.group(1))
    assert number_match is not None
    number = f"{int(number_match.group(1))}{number_match.group(2) or ''}{number_match.group(3).upper()}"
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
        normalized[ordinal_index] = _ORDINALS.get(normalized[ordinal_index], normalized[ordinal_index])
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


def _street_endpoints(street_address: str) -> tuple[str, ...]:
    match = _HOUSE_RANGE.match(street_address)
    if not match:
        return (street_address.strip(),)
    first, last, street = match.groups()
    return tuple(dict.fromkeys((f"{first} {street}", f"{last} {street}")))


def _zip_is_plausible(zip_code: str, state: str) -> bool:
    return bool(re.fullmatch(r"\d{5}", zip_code)) and zip_code.startswith(
        _ZIP_PREFIXES.get(state.upper(), tuple(str(i) for i in range(10)))
    )


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
        if address.zip and _zip_is_plausible(address.zip, address.state):
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
        return None, [LookupAttempt(query, "invalid_input", "Missing or unsupported house number")], []
    for query in _queries(street, address, aliases):
        try:
            response = geocoder.lookup(query)
        except CacheMissError as exc:
            attempts.append(LookupAttempt(query, "cache_miss", str(exc)))
            continue
        except GeocoderError as exc:
            attempts.append(LookupAttempt(query, "service_error", str(exc)))
            continue
        candidates_seen.extend(response.candidates)
        credible = [item for item in response.candidates if _credible(item, query)]
        places = {item.city_geoid for item in credible}
        if len(places) == 1:
            selected = next(
                (item for item in credible if _street_match_kind(query, item) == "exact"),
                credible[0],
            )
            kind = _street_match_kind(query, selected)
            attempts.append(LookupAttempt(
                query, "match" if kind == "exact" else "normalized_match", selected.matched_address
            ))
            return selected, attempts, candidates_seen
        if len(places) > 1:
            attempts.append(LookupAttempt(query, "ambiguous", "Multiple incorporated places"))
        elif response.candidates:
            attempts.append(LookupAttempt(query, "rejected", "No candidate passed address and place checks"))
        else:
            attempts.append(LookupAttempt(query, "no_match"))
    return None, attempts, candidates_seen


def choose_resolution(
    address: AddressInput,
    endpoint_results: Sequence[GeocodeCandidate | None],
    attempts: Sequence[LookupAttempt],
    candidates: Sequence[GeocodeCandidate],
    search_aliases: Mapping[str, str] = DEFAULT_SEARCH_ALIASES,
) -> ResolvedAddress:
    """Make the final decision from supplied evidence; performs no I/O."""
    warnings: list[str] = []
    if not address.zip:
        warnings.append("missing_input_zip")
    elif not _zip_is_plausible(address.zip, address.state):
        warnings.append("suspicious_input_zip")
    if len(endpoint_results) > 1:
        warnings.append("house_number_range_checked_at_both_endpoints")
    if any(attempt.outcome == "normalized_match" for attempt in attempts):
        warnings.append("street_normalized_match")

    selected: GeocodeCandidate | None = None
    if endpoint_results and all(item is not None for item in endpoint_results):
        resolved_candidates = [item for item in endpoint_results if item is not None]
        if len({item.city_geoid for item in resolved_candidates}) == 1:
            selected = resolved_candidates[0]
        else:
            warnings.append("range_endpoints_disagree")
    if selected is not None:
        expected_city = search_aliases.get(
            address.postal_city.strip().casefold(), address.postal_city.strip()
        )
        if expected_city and selected.city and selected.city.casefold() != expected_city.casefold():
            warnings.append("postal_city_differs_from_legal_city")
        if address.zip and selected.zip and address.zip != selected.zip:
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
        attempts: list[LookupAttempt] = []
        candidates: list[GeocodeCandidate] = []
        for street in _street_endpoints(address.street_address):
            chosen, endpoint_attempts, endpoint_candidates = _lookup_endpoint(
                street, address, geocoder, search_aliases
            )
            endpoint_results.append(chosen)
            attempts.extend(endpoint_attempts)
            candidates.extend(endpoint_candidates)
        output.append(choose_resolution(address, endpoint_results, attempts, candidates, search_aliases))
    return output


def apply_review_overrides(
    results: Sequence[ResolvedAddress], overrides: Sequence[ReviewOverride]
) -> list[ResolvedAddress]:
    """Apply source-backed human decisions while preserving one result per input."""
    by_id: dict[str, ReviewOverride] = {}
    for override in overrides:
        if override.address_id in by_id:
            raise ValueError(f"Duplicate review override: {override.address_id}")
        if not all((override.input_postal_city, override.input_state,
                    override.legal_state, override.legal_city, override.reason,
                    override.reviewer, override.reviewed_at)):
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
        if (override.input_postal_city.casefold() != result.input.postal_city.strip().casefold()
                or override.input_state.upper() != result.input.state.upper()):
            raise ValueError(f"Review override has stale city or state: {result.address_id}")
        warnings = [warning for warning in result.warnings if warning != "jurisdiction_needs_review"]
        warnings.append("review_override_applied")
        if override.legal_state.upper() != result.input.state.upper():
            warnings.append("reviewed_state_differs_from_input")
        output.append(replace(
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
        ))
    return output
