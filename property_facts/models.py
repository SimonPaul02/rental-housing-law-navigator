"""Storage-independent inputs and evidence-bearing property facts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from typing import Generic, Literal, TypeVar


FactStatus = Literal["present", "not_supplied", "invalid", "conflicted"]
T = TypeVar("T")


@dataclass(frozen=True)
class Provenance:
    source_dataset: str
    source_field: str
    retrieved_at: str
    source_url: str | None = None


@dataclass(frozen=True)
class PropertyInput:
    """One property row. Raw fields can carry their own evidence sources."""

    address_id: str
    year_built: str = ""
    units: str = ""
    use_code: str = ""
    use_description: str = ""
    source_dataset: str = ""
    retrieved_at: str = ""
    first_built_date: str = ""
    certificate_of_occupancy_date: str = ""
    record_scope: str = "source_address_row"
    field_provenance: Mapping[str, Provenance] = field(default_factory=dict)


@dataclass(frozen=True)
class Fact(Generic[T]):
    """A typed value, its distinct meaning, and the exact source value."""

    kind: str
    value: T | None
    raw_value: str
    status: FactStatus
    provenance: Provenance


@dataclass(frozen=True)
class PropertyFacts:
    address_id: str
    record_scope: str
    year_built: Fact[int]
    units: Fact[int]
    use_code: Fact[str]
    use_description: Fact[str]
    first_built_date: Fact[date]
    certificate_of_occupancy_date: Fact[date]
    warnings: tuple[str, ...] = ()
