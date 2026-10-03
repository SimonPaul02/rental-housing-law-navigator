"""Census geocoder adapter and a persistent JSONL response cache."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import AddressQuery, GeocodeCandidate, GeocodeResponse
from .ports import CacheMissError, Geocoder, GeocoderError


class CensusGeocoder:
    URL = "https://geocoding.geo.census.gov/geocoder/geographies/address"

    def __init__(
        self,
        *,
        benchmark: str = "Public_AR_Current",
        vintage: str = "Current_Current",
        timeout: float = 20.0,
        retries: int = 2,
        min_interval: float = 0.1,
        url: str | None = None,
    ) -> None:
        self.benchmark = benchmark
        self.vintage = vintage
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval
        self.url = url or self.URL
        self._last_request_at = 0.0

    def lookup(self, query: AddressQuery) -> GeocodeResponse:
        params = {
            "street": query.street,
            "city": query.city,
            "state": query.state,
            "benchmark": self.benchmark,
            "vintage": self.vintage,
            "format": "json",
        }
        if query.zip:
            params["zip"] = query.zip
        request = Request(
            f"{self.url}?{urlencode(params)}", headers={"User-Agent": "housing-law-navigator/0.1"}
        )

        for attempt in range(self.retries + 1):
            try:
                pause = self.min_interval - (time.monotonic() - self._last_request_at)
                if pause > 0:
                    time.sleep(pause)
                self._last_request_at = time.monotonic()
                with urlopen(request, timeout=self.timeout) as response:
                    raw = json.load(response)
                if raw.get("errors"):
                    raise GeocoderError(f"Census API error: {raw['errors']}")
                return self._parse(raw)
            except HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt == self.retries:
                    raise GeocoderError(f"Census HTTP {exc.code}") from exc
            except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                if attempt == self.retries:
                    raise GeocoderError(f"Census request failed: {exc}") from exc
            time.sleep(0.5 * (2**attempt))

        raise AssertionError("unreachable")

    def _parse(self, raw: dict) -> GeocodeResponse:
        matches = raw.get("result", {}).get("addressMatches", [])
        candidates = []
        for match in matches:
            geography = match.get("geographies") or {}
            places = geography.get("Incorporated Places") or []
            counties = geography.get("Counties") or []
            components = match.get("addressComponents") or {}
            coordinates = match.get("coordinates") or {}
            matched_address = match.get("matchedAddress") or ""
            if "x" not in coordinates or "y" not in coordinates:
                continue
            # Multiple incorporated-place entries are left ambiguous for review.
            place = places[0] if len(places) == 1 else None
            county = counties[0] if len(counties) == 1 else None
            candidates.append(
                GeocodeCandidate(
                    matched_address=matched_address,
                    street_address=matched_address.split(",", 1)[0],
                    state=components.get("state", ""),
                    county=(county.get("BASENAME") or county.get("NAME")) if county else None,
                    city=(place.get("BASENAME") or place.get("NAME")) if place else None,
                    city_geoid=str(place.get("GEOID")) if place and place.get("GEOID") else None,
                    longitude=float(coordinates["x"]),
                    latitude=float(coordinates["y"]),
                    benchmark=self.benchmark,
                    vintage=self.vintage,
                    zip=components.get("zip") or None,
                )
            )
        return GeocodeResponse(tuple(candidates), raw)


class CachedGeocoder:
    """Append successful API responses so a rerun can be fully offline."""

    def __init__(
        self,
        path: str | Path,
        live: Geocoder | None = None,
        *,
        benchmark: str = "Public_AR_Current",
        vintage: str = "Current_Current",
    ) -> None:
        self.path = Path(path)
        self.live = live
        self.benchmark = getattr(live, "benchmark", benchmark)
        self.vintage = getattr(live, "vintage", vintage)
        self.entries: dict[str, GeocodeResponse] = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    self.entries[record["key"]] = GeocodeResponse(
                        tuple(GeocodeCandidate(**item) for item in record["candidates"]),
                        record.get("raw_response", {}),
                    )

    def lookup(self, query: AddressQuery) -> GeocodeResponse:
        key = json.dumps(
            {"query": asdict(query), "benchmark": self.benchmark, "vintage": self.vintage},
            sort_keys=True,
            separators=(",", ":"),
        )
        if key in self.entries:
            return self.entries[key]
        if self.live is None:
            raise CacheMissError("No cached response for this query; offline mode is enabled")
        response = self.live.lookup(query)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "key": key,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "candidates": [asdict(item) for item in response.candidates],
            "raw_response": response.raw_response,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.entries[key] = response
        return response
