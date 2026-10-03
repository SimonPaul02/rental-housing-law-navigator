"""Check the resolver-to-database boundary without a network or database."""

from __future__ import annotations

import pytest

from app.db.models import Address
from app.modules.address_lookup import service
from app.modules.address_lookup.address_resolution.models import AddressInput, ResolvedAddress


class RecordingSession:
    def __init__(self) -> None:
        self.added = []
        self.flushed = False

    def add(self, row) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        self.flushed = True


@pytest.mark.asyncio
async def test_unresolved_result_stays_unresolved_in_backend(monkeypatch) -> None:
    address = Address(
        address_id="A1",
        street_address="12 OAK ST",
        postal_city="Newark",
        state="NJ",
    )
    result = ResolvedAddress(
        address_id="A1",
        input=AddressInput("A1", "12 OAK ST", "Newark", "NJ"),
        status="needs_review",
        legal_state=None,
        legal_county=None,
        legal_city=None,
        city_geoid=None,
        longitude=None,
        latitude=None,
        matched_address=None,
        benchmark=None,
        vintage=None,
        warnings=("jurisdiction_needs_review",),
    )
    monkeypatch.setattr(service, "_resolve_batch", lambda inputs: [result])
    session = RecordingSession()

    rows = await service.resolve_many(session, [address])

    assert session.flushed
    assert rows == session.added
    assert rows[0].method == "unresolved"
    assert rows[0].legal_city is None
    assert rows[0].resolution_evidence["status"] == "needs_review"
