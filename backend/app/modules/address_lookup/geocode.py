"""Census Geocoder client - resolves a mailing address to its legal place.

`postal_city` in the sample data is the mailing city, which is often not the
legal city: "Van Nuys" is inside the City of Los Angeles and Boston rows may
say "Dorchester". The Incorporated Places layer gives us the real one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

# Census returns place names like "Los Angeles city" / "Boston city".
_PLACE_SUFFIXES = (
    " city",
    " town",
    " village",
    " borough",
    " township",
    " CDP",
    " municipality",
)


def clean_place_name(name: str) -> str:
    for suffix in _PLACE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name.strip()


@dataclass(slots=True)
class GeocodeResult:
    legal_city: str | None
    legal_state: str | None
    county: str | None
    matched_address: str | None
    latitude: float | None
    longitude: float | None
    place_geoid: str | None
    method: str
    confidence: float | None
    note: str | None = None


async def geocode_address(
    client: httpx.AsyncClient,
    *,
    street: str,
    city: str,
    state: str,
    zipcode: str | None,
) -> GeocodeResult:
    """One-line address lookup against the Census geographies endpoint.

    Falls back to the postal city - clearly labelled - when Census returns no
    match, so Module B always has something to reason about rather than
    dropping the address.
    """
    params = {
        "street": street,
        "city": city,
        "state": state,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "layers": "Incorporated Places,Counties",
        "format": "json",
    }
    if zipcode:
        params["zip"] = zipcode

    try:
        response = await client.get(
            settings.census_geocoder_url,
            params=params,
            timeout=settings.geocoder_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("geocoder error for %s, %s %s: %s", street, city, state, exc)
        return _postal_fallback(city, state, f"geocoder unavailable: {exc}")

    matches = (data.get("result") or {}).get("addressMatches") or []
    if not matches:
        return _postal_fallback(city, state, "no Census address match")

    match = matches[0]
    geographies = match.get("geographies") or {}
    places = geographies.get("Incorporated Places") or []
    counties = geographies.get("Counties") or []
    coords = match.get("coordinates") or {}

    place = places[0] if places else None
    legal_city = clean_place_name(place["NAME"]) if place else None

    return GeocodeResult(
        legal_city=legal_city,
        legal_state=state.upper(),
        county=clean_place_name(counties[0]["NAME"]) if counties else None,
        matched_address=match.get("matchedAddress"),
        latitude=coords.get("y"),
        longitude=coords.get("x"),
        place_geoid=place.get("GEOID") if place else None,
        method="census",
        confidence=0.95 if place else 0.6,
        note=None
        if place
        else "Census matched the address but it sits in no incorporated place "
        "(unincorporated area); city law may not apply.",
    )


def _postal_fallback(city: str, state: str, why: str) -> GeocodeResult:
    return GeocodeResult(
        legal_city=city,
        legal_state=state.upper(),
        county=None,
        matched_address=None,
        latitude=None,
        longitude=None,
        place_geoid=None,
        method="postal_fallback",
        confidence=0.3,
        note=f"Fell back to the postal city ({why}). The legal city may differ.",
    )
