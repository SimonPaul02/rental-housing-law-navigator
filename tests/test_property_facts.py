"""Behavioral checks for independent property facts and its resolver adapter."""

from dataclasses import replace
from pathlib import Path
import unittest

from address_resolution.io import read_in_from_csv
from address_resolution.models import AddressInput, ResolvedAddress
from pipeline.property_facts_adapter import from_address_inputs, from_resolved_addresses
from property_facts import PropertyInput, Provenance, build_property_facts


class PropertyFactsTests(unittest.TestCase):
    def test_missing_values_keep_their_meaning_and_source(self) -> None:
        row = PropertyInput(
            "A0005", use_code="7700", use_description="Alameda County use code (5+ units)",
            source_dataset="Alameda County parcels", retrieved_at="2026-10-01T22:50Z",
        )
        result = build_property_facts([row])[0]

        self.assertIsNone(result.units.value)
        self.assertEqual(result.units.status, "not_supplied")
        self.assertIsNone(result.year_built.value)
        self.assertIsNone(result.certificate_of_occupancy_date.value)
        self.assertEqual(result.use_code.value, "7700")
        self.assertEqual(result.use_code.provenance.source_dataset, "Alameda County parcels")
        self.assertEqual(result.units.provenance.source_field, "units")
        self.assertEqual(result.warnings, ())

    def test_year_built_never_becomes_certificate_date(self) -> None:
        base = PropertyInput("A1", year_built="1978", retrieved_at="2026-10-01T22:50Z")
        result = build_property_facts([base])[0]
        self.assertEqual(result.year_built.value, 1978)
        self.assertIsNone(result.certificate_of_occupancy_date.value)

        sourced = build_property_facts([
            replace(
                base,
                certificate_of_occupancy_date="1979-06-13",
                field_provenance={
                    "certificate_of_occupancy_date": Provenance(
                        "Public certificate registry", "occupancy_issued_on", "2026-10-02T10:00Z",
                        "https://example.org/certificate/1",
                    )
                },
            )
        ])[0]
        self.assertEqual(sourced.certificate_of_occupancy_date.value.isoformat(), "1979-06-13")
        self.assertEqual(sourced.certificate_of_occupancy_date.provenance.source_field,
                         "occupancy_issued_on")
        self.assertEqual(sourced.certificate_of_occupancy_date.provenance.source_dataset,
                         "Public certificate registry")

    def test_conflicting_values_are_preserved_and_flagged(self) -> None:
        rows = [
            PropertyInput("A0041", units="15", use_description="Apartment 5 to 14 Units"),
            PropertyInput("A0398", units="5", use_description="TIC Bldg 4 units or less"),
        ]
        results = build_property_facts(rows)
        self.assertEqual([item.units.value for item in results], [15, 5])
        for result in results:
            self.assertEqual(result.units.status, "conflicted")
            self.assertEqual(result.use_description.status, "conflicted")
            self.assertIn("units_use_description_conflict", result.warnings)

    def test_invalid_values_keep_raw_input_and_do_not_become_zero(self) -> None:
        result = build_property_facts([PropertyInput(
            "A1", year_built="197X", units="0", first_built_date="1978",
            certificate_of_occupancy_date="1979-02-30", retrieved_at="not-a-time",
        )])[0]
        for field in ("year_built", "units", "first_built_date", "certificate_of_occupancy_date"):
            self.assertIsNone(getattr(result, field).value)
            self.assertEqual(getattr(result, field).status, "invalid")
            self.assertIn(f"invalid_{field}", result.warnings)
        self.assertEqual(result.year_built.raw_value, "197X")
        self.assertIn("invalid_retrieved_at", result.warnings)

    def test_rejects_duplicate_or_missing_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate address_id"):
            build_property_facts([PropertyInput("A1"), PropertyInput("A1")])
        with self.assertRaisesRegex(ValueError, "Missing address_id"):
            build_property_facts([PropertyInput(" ")])

    def test_future_construction_fact_needs_review(self) -> None:
        result = build_property_facts([PropertyInput(
            "A1", year_built="2027", certificate_of_occupancy_date="2027-01-01",
            retrieved_at="2026-10-01T22:50Z",
        )])[0]
        self.assertEqual(result.year_built.value, 2027)
        self.assertEqual(result.year_built.status, "conflicted")
        self.assertEqual(result.certificate_of_occupancy_date.status, "conflicted")
        self.assertIn("year_built_after_retrieval", result.warnings)

    def test_resolved_adapter_uses_input_even_when_jurisdiction_is_unresolved(self) -> None:
        address = AddressInput("A1", "12 OAK ST", "Newark", "NJ", units="5")
        unresolved = ResolvedAddress(
            address_id="A1", input=address, status="needs_review", legal_state=None,
            legal_county=None, legal_city=None, city_geoid=None, longitude=None,
            latitude=None, matched_address=None, benchmark=None, vintage=None,
        )
        self.assertEqual(from_resolved_addresses([unresolved])[0].units.value, 5)
        with self.assertRaisesRegex(ValueError, "mismatch"):
            from_resolved_addresses([replace(unresolved, address_id="A2")])

    def test_all_sample_rows_have_a_property_record(self) -> None:
        csv_path = Path(__file__).resolve().parents[1] / "data" / "sample_addresses.csv"
        inputs = read_in_from_csv(csv_path)
        records = from_address_inputs(inputs)
        self.assertEqual(len(records), 500)
        self.assertEqual([record.address_id for record in records],
                         [address.address_id for address in inputs])
        self.assertEqual(sum(record.year_built.value is None for record in records), 212)
        self.assertEqual(sum(record.units.value is None for record in records), 242)
        self.assertEqual(
            {record.address_id for record in records if "units_use_description_conflict" in record.warnings},
            {"A0041", "A0398"},
        )


if __name__ == "__main__":
    unittest.main()
