"""ZIP quality stays separate from legal jurisdiction and source-backed review."""

from __future__ import annotations

import csv
import datetime as dt
import json
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.db.models import Address, AddressJurisdiction, AddressZipReview
from app.modules.address_lookup import router, service
from app.modules.address_lookup.adapters.address_files import write_zip_review_csv
from app.modules.address_lookup.adapters.zip_reviews import read_zip_reviews_csv
from app.modules.address_lookup.address_resolution.models import (
    AcceptedEndpoint,
    AddressInput,
    GeocodeCandidate,
    ResolvedAddress,
    ZipAssessment,
)
from app.modules.address_lookup.address_resolution.zip_assessment import assess_zip
from app.modules.address_lookup.schemas import JurisdictionRecord
from app.modules.address_lookup.zip_reviews import ZipReviewDecision, validate_decision


def accepted(street: str, zip_code: str | None) -> AcceptedEndpoint:
    return AcceptedEndpoint(
        input_street=street,
        match_kind="exact",
        candidate=GeocodeCandidate(
            matched_address=f"{street}, NEWARK, NJ, {zip_code or ''}",
            street_address=street,
            state="NJ",
            county="Essex",
            city="Newark",
            city_geoid="3451000",
            longitude=-74.0,
            latitude=40.0,
            benchmark="Public_AR_Current",
            vintage="Current_Current",
            zip=zip_code,
        ),
    )


def test_zip_status_requires_complete_accepted_evidence() -> None:
    address = AddressInput("A1", "12-14 OAK ST", "Newark", "NJ", "07102")
    partial = assess_zip(address, [accepted("12 OAK ST", "07102")], 2)
    assert partial.status == "insufficient_evidence"
    assert partial.matched_zips == ("07102",)
    assert (
        assess_zip(address, [accepted("12 OAK ST", None), accepted("14 OAK ST", "07102")], 2).status
        == "insufficient_evidence"
    )
    assert assess_zip(replace(address, zip=""), [], 2).status == "missing_input"
    assert assess_zip(replace(address, zip="11219"), [], 2).status == "invalid_for_state"


def test_zip_evidence_is_exposed_without_changing_jurisdiction() -> None:
    address = AddressInput("A1", "12 OAK ST", "Newark", "NJ", "07102")
    assessment = assess_zip(address, [accepted("12 OAK ST", "07103")], 1)
    row = AddressJurisdiction(
        address_id="A1",
        legal_city="Newark",
        legal_state="NJ",
        method="geocoder",
        resolution_evidence={"zip_assessment": asdict(assessment)},
    )
    payload = JurisdictionRecord.model_validate(row)
    assert payload.legal_city == "Newark"
    assert payload.zip_assessment.status == "mismatch"
    assert payload.zip_assessment.matched_zips == ["07103"]


def test_backfill_merges_zip_evidence_without_replacing_existing_evidence() -> None:
    from app.modules.address_lookup.address_resolution.models import ResolvedAddress

    address = AddressInput("A1", "12 OAK ST", "Newark", "NJ", "07102")
    endpoint = accepted("12 OAK ST", "07103")
    assessment = assess_zip(address, [endpoint], 1)
    result = ResolvedAddress(
        address_id="A1",
        input=address,
        status="resolved",
        legal_state="NJ",
        legal_county="Essex",
        legal_city="Newark",
        city_geoid="3451000",
        longitude=-74.0,
        latitude=40.0,
        matched_address=endpoint.candidate.matched_address,
        benchmark="Public_AR_Current",
        vintage="Current_Current",
        accepted_endpoints=(endpoint,),
        zip_assessment=assessment,
    )
    existing = {"input": asdict(address), "legal_city": "Newark", "review_override": None}
    updated = service.merge_zip_evidence(existing, result)
    assert updated["legal_city"] == "Newark"
    assert updated["zip_assessment"]["status"] == "mismatch"
    assert len(updated["accepted_endpoints"]) == 1
    assert existing.get("zip_assessment") is None
    with pytest.raises(ValueError, match="changed"):
        service.merge_zip_evidence({"input": {**asdict(address), "zip": "07030"}}, result)


def test_zip_review_requires_current_input_and_public_evidence() -> None:
    address = AddressInput("A1", "12 OAK ST", "Newark", "NJ", "11219")
    assessment = assess_zip(address, [accepted("12 OAK ST", "07102")], 1)
    decision = ZipReviewDecision(
        address_id="A1",
        input_street_address="12 OAK ST",
        input_postal_city="Newark",
        input_state="NJ",
        input_zip="11219",
        decision="census_zip_supported",
        confirmed_zip="07102",
        source_url="https://example.gov/parcel",
        reason="Verified against official parcel address",
        reviewer="reviewer-1",
        reviewed_at=dt.date(2026, 10, 4),
    )
    validate_decision(decision, address, assessment)
    with pytest.raises(ValueError, match="stale"):
        validate_decision(replace(decision, input_zip="07102"), address, assessment)
    with pytest.raises(ValueError, match="public source URL"):
        validate_decision(replace(decision, source_url="private note"), address, assessment)
    with pytest.raises(ValueError, match="accepted Census"):
        validate_decision(replace(decision, confirmed_zip="07103"), address, assessment)


def test_zip_review_csv_adapter_parses_review_date() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "reviews.csv"
        path.write_text(
            "address_id,input_street_address,input_postal_city,input_state,input_zip,"
            "decision,confirmed_zip,source_url,reason,reviewer,reviewed_at\n"
            "A1,12 OAK ST,Newark,NJ,11219,census_zip_supported,07102,"
            "https://example.gov/parcel,Verified,reviewer-1,2026-10-04\n",
            encoding="utf-8",
        )
        reviews = read_zip_reviews_csv(path)
    assert len(reviews) == 1
    assert reviews[0].reviewed_at == dt.date(2026, 10, 4)


def test_checked_in_garfield_repair_matches_the_current_source_row() -> None:
    data_dir = Path(__file__).resolve().parents[2] / "data"
    decision = read_zip_reviews_csv(data_dir / "zip_reviews.csv")[0]
    record = next(
        row
        for row in json.loads((data_dir / "resolved_addresses.json").read_text())
        if row["address_id"] == decision.address_id
    )
    address = AddressInput(**record["input"])
    assessment = ZipAssessment(**record["zip_assessment"])
    validate_decision(decision, address, assessment)
    assert decision.input_zip == "07304"
    assert decision.confirmed_zip == "07305"
    assert "mailing address" in decision.reason


def test_source_backed_zip_repair_is_visible_in_review_export() -> None:
    address = AddressInput("A1", "12 OAK ST", "Newark", "NJ", "11219")
    endpoint = accepted("12 OAK ST", "07102")
    result = ResolvedAddress(
        address_id="A1",
        input=address,
        status="resolved",
        legal_state="NJ",
        legal_county="Essex",
        legal_city="Newark",
        city_geoid="3451000",
        longitude=-74.0,
        latitude=40.0,
        matched_address=endpoint.candidate.matched_address,
        benchmark="Public_AR_Current",
        vintage="Current_Current",
        zip_assessment=assess_zip(address, [endpoint], 1),
    )
    decision = ZipReviewDecision(
        address_id="A1",
        input_street_address="12 OAK ST",
        input_postal_city="Newark",
        input_state="NJ",
        input_zip="11219",
        decision="census_zip_supported",
        confirmed_zip="07102",
        source_url="https://example.gov/parcel",
        reason="Public parcel record confirms the property ZIP",
        reviewer="reviewer-1",
        reviewed_at=dt.date(2026, 10, 4),
    )
    with TemporaryDirectory() as directory:
        path = Path(directory) / "zip_review.csv"
        write_zip_review_csv([result], path, [decision])
        with path.open(newline="", encoding="utf-8") as stream:
            row = next(csv.DictReader(stream))
        assert row["input_zip"] == "11219"
        assert row["confirmed_property_zip"] == "07102"
        assert row["review_source_url"] == decision.source_url
        with pytest.raises(ValueError, match="stale"):
            write_zip_review_csv([result], path, [replace(decision, input_zip="07103")])


@pytest.mark.asyncio
async def test_review_api_shows_current_human_finding_separately() -> None:
    address = Address(
        address_id="A1",
        street_address="12 OAK ST",
        postal_city="Newark",
        state="NJ",
        zip="11219",
    )
    assessment = assess_zip(service.input_from_db(address), [accepted("12 OAK ST", "07102")], 1)
    address.jurisdiction = AddressJurisdiction(
        address_id="A1",
        legal_city="Newark",
        legal_state="NJ",
        method="geocoder",
        resolution_evidence={"zip_assessment": asdict(assessment)},
    )
    address.zip_reviews = [
        AddressZipReview(
            id=1,
            address_id="A1",
            input_street_address="12 OAK ST",
            input_postal_city="Newark",
            input_state="NJ",
            input_zip="11219",
            decision="census_zip_supported",
            confirmed_zip="07102",
            source_url="https://example.gov/parcel",
            reason="Verified",
            reviewer="reviewer-1",
            reviewed_at=dt.date(2026, 10, 4),
        )
    ]

    class Session:
        async def execute(self, statement):
            return self

        def scalars(self):
            return self

        def all(self):
            return [address]

    cases = await router.list_zip_review_cases(session=Session())
    assert len(cases) == 1
    assert cases[0].legal_city == "Newark"
    assert cases[0].assessment.status == "invalid_for_state"
    assert cases[0].review.current is True
    address.zip = "07102"
    assert (await router.list_zip_review_cases(session=Session()))[0].review.current is False
