"""External lookup contract used by the storage-independent resolver."""

from typing import Protocol

from .models import AddressQuery, GeocodeResponse


class GeocoderError(RuntimeError):
    """A lookup failed for a reason other than a valid no-match response."""


class Geocoder(Protocol):
    def lookup(self, query: AddressQuery) -> GeocodeResponse: ...
