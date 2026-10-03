"""Address-to-jurisdiction resolution for the housing-law navigator."""

from .models import AddressInput, ResolvedAddress
from .resolver import resolve_addresses

__all__ = ["AddressInput", "ResolvedAddress", "resolve_addresses"]
