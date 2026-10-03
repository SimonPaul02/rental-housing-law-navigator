"""Address-to-jurisdiction resolution for the housing-law navigator."""

from .models import AddressInput, ResolvedAddress, ReviewOverride
from .resolver import apply_review_overrides, resolve_addresses

__all__ = [
    "AddressInput", "ResolvedAddress", "ReviewOverride",
    "resolve_addresses", "apply_review_overrides",
]
