"""Resolve addresses without knowing how input or output is stored."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

from .models import (
    AddressInput,
    AddressQuery,
    GeocodeCandidate,
    LookupAttempt,
    ResolvedAddress,
)
from .ports import Geocoder, GeocoderError


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

_HOUSE_RANGE = re.compile(r"^\s*(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\s+(.+)$")
_HOUSE_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+(.+)$")
_SUFFIXES = {
    "AV": "AVE", "AVENUE": "AVE", "BL": "BLVD", "BOULEVARD": "BLVD",
    "DRIVE": "DR", "LANE": "LN", "PLACE": "PL", "ROAD": "RD",
    "STREET": "ST", "TERRACE": "TER", "WAY": "WAY",
}
_ZIP_PREFIXES = {"CA": ("9",), "NJ": ("0",), "MA": ("0",)}


def _house_number_and_street(value: str) -> tuple[str | None, tuple[str, ...]]:
    match = _HOUSE_NUMBER.match(value.split(",", 1)[0])
    if not match:
        return None, ()
    number = match.group(1).lstrip("0") or "0"
    words = re.findall(r"[A-Z0-9]+", match.group(2).upper())
    words = [re.sub(r"^0+(\d+)(ST|ND|RD|TH)$", r"\1\2", word) for word in words]
    return number, tuple(_SUFFIXES.get(word, word) for word in words)


def _street_endpoints(street_address: str) -> tuple[str, ...]:
    match = _HOUSE_RANGE.match(street_address)
    if not match:
        return (street_address.strip(),)
    first, last, street = match.groups()
    return (f"{first} {street}", f"{last} {street}")


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
    expected_number, expected_street = _house_number_and_street(query.street)
    matched_number, matched_street = _house_number_and_street(candidate.street_address)
    return (
        bool(candidate.city and candidate.city_geoid)
        and candidate.state.upper() == query.state.upper()
        and expected_number is not None
        and expected_number == matched_number
        and bool(expected_street)
        and expected_street == matched_street
    )


def _lookup_endpoint(
    street: str,
    address: AddressInput,
    geocoder: Geocoder,
    aliases: Mapping[str, str],
) -> tuple[GeocodeCandidate | None, list[LookupAttempt], list[GeocodeCandidate]]:
    attempts: list[LookupAttempt] = []
    candidates_seen: list[GeocodeCandidate] = []
    for query in _queries(street, address, aliases):
        try:
            response = geocoder.lookup(query)
        except GeocoderError as exc:
            attempts.append(LookupAttempt(query, "error", str(exc)))
            continue
        candidates_seen.extend(response.candidates)
        credible = [item for item in response.candidates if _credible(item, query)]
        places = {item.city_geoid for item in credible}
        if len(places) == 1:
            attempts.append(LookupAttempt(query, "match", credible[0].matched_address))
            return credible[0], attempts, candidates_seen
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
            warnings.append("legal_city_differs_from_search_hint")
            selected = None
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
