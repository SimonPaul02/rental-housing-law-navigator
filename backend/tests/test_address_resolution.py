"""Boundary and missing-data checks using a fake geocoder, with no network."""

import csv
import json
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from app.modules.address_lookup.adapters.address_files import (
    read_in_from_csv,
    read_review_overrides_csv,
    write_review_csv,
    write_to_internal_json,
    write_zip_review_csv,
)
from app.modules.address_lookup.address_resolution.census import CachedGeocoder, CensusGeocoder
from app.modules.address_lookup.address_resolution.models import (
    AddressInput,
    AddressQuery,
    GeocodeCandidate,
    GeocodeResponse,
    ReviewOverride,
)
from app.modules.address_lookup.address_resolution.ports import GeocoderError
from app.modules.address_lookup.address_resolution.resolver import (
    apply_review_overrides,
    resolve_addresses,
)


def candidate(street: str, city: str, geoid: str, state: str = "NJ") -> GeocodeCandidate:
    return GeocodeCandidate(
        matched_address=f"{street}, {city}, {state}",
        street_address=street,
        state=state,
        county=None,
        city=city,
        city_geoid=geoid,
        longitude=-74.0,
        latitude=40.0,
        benchmark="Public_AR_Current",
        vintage="Current_Current",
    )


class FakeGeocoder:
    def __init__(self, matches: dict[str, list[GeocodeCandidate]]) -> None:
        self.matches = matches
        self.queries: list[AddressQuery] = []

    def lookup(self, query: AddressQuery) -> GeocodeResponse:
        self.queries.append(query)
        return GeocodeResponse(tuple(self.matches.get(query.street, [])))


class AddressResolutionTests(unittest.TestCase):
    def test_wrong_zip_is_not_used_to_decide_city(self) -> None:
        address = AddressInput("A0003", "876 S 14TH ST", "Newark", "NJ", "11219")
        geocoder = FakeGeocoder(
            {"876 S 14TH ST": [candidate("876 S 14TH ST", "Newark", "3401351000")]}
        )

        result = resolve_addresses([address], geocoder)[0]

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.legal_city, "Newark")
        self.assertIn("suspicious_input_zip", result.warnings)
        self.assertEqual(geocoder.queries[0].zip, "")

    def test_matched_zip_disagreement_is_visible(self) -> None:
        address = AddressInput("A0489", "204 GRAND ST", "Hoboken", "NJ", "06901")
        match = replace(candidate("204 GRAND ST", "Hoboken", "3401332250"), zip="07030")
        result = resolve_addresses([address], FakeGeocoder({"204 GRAND ST": [match]}))[0]
        self.assertEqual(result.status, "resolved")
        self.assertIn("suspicious_input_zip", result.warnings)
        self.assertIn("input_zip_differs_from_match", result.warnings)
        self.assertEqual(result.zip_assessment.status, "invalid_for_state")
        self.assertEqual(result.zip_assessment.matched_zips, ("07030",))

    def test_zip_assessment_uses_accepted_candidates_only(self) -> None:
        address = AddressInput("A1", "12 OAK ST", "Newark", "NJ", "07102")
        good = replace(candidate("12 OAK ST", "Newark", "3451000"), zip="07102")
        wrong_house = replace(candidate("14 OAK ST", "Newark", "3451000"), zip="07030")
        result = resolve_addresses(
            [address],
            FakeGeocoder(
                {
                    "12 OAK ST": [good, wrong_house],
                }
            ),
        )[0]
        self.assertEqual(result.zip_assessment.status, "matches_all")
        self.assertEqual(result.zip_assessment.matched_zips, ("07102",))
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(len(result.accepted_endpoints), 1)

    def test_range_zip_assessment_checks_both_endpoints(self) -> None:
        address = AddressInput("A1", "12-14 OAK ST", "Newark", "NJ", "07102")
        first = replace(candidate("12 OAK ST", "Newark", "3451000"), zip="07102")
        second = replace(candidate("14 OAK ST", "Newark", "3451000"), zip="07103")
        result = resolve_addresses(
            [address],
            FakeGeocoder(
                {
                    "12 OAK ST": [first],
                    "14 OAK ST": [second],
                }
            ),
        )[0]
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.zip_assessment.status, "matches_some_endpoints")
        self.assertEqual(result.zip_assessment.matched_zips, ("07102", "07103"))
        self.assertIn("input_zip_differs_from_match", result.warnings)

    def test_zip_review_export_keeps_source_and_jurisdiction_separate(self) -> None:
        address = AddressInput(
            "A1",
            "12 OAK ST",
            "Newark",
            "NJ",
            "11219",
            source_dataset="assessor",
            retrieved_at="2026-10-01",
        )
        match = replace(candidate("12 OAK ST", "Newark", "3451000"), zip="07102")
        result = resolve_addresses([address], FakeGeocoder({"12 OAK ST": [match]}))[0]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "zip_review.csv"
            write_zip_review_csv([result], path)
            text = path.read_text(encoding="utf-8")
        self.assertIn("A1,resolved", text)
        self.assertIn("invalid_for_state", text)
        self.assertIn("assessor", text)
        self.assertEqual(result.legal_city, "Newark")
        self.assertEqual(result.input.zip, "11219")

    def test_neighborhood_alias_is_only_a_search_hint(self) -> None:
        address = AddressInput("A0065", "12 TEST ST", "Dorchester", "MA", "")

        class AliasGeocoder(FakeGeocoder):
            def lookup(self, query: AddressQuery) -> GeocodeResponse:
                self.queries.append(query)
                if query.city == "Boston":
                    return GeocodeResponse((candidate("12 TEST ST", "Boston", "2507000", "MA"),))
                return GeocodeResponse(())

        geocoder = AliasGeocoder({})
        result = resolve_addresses([address], geocoder)[0]
        self.assertEqual(result.legal_city, "Boston")
        self.assertEqual([query.city for query in geocoder.queries], ["Dorchester", "Boston"])

    def test_range_needs_both_endpoints_in_same_city(self) -> None:
        address = AddressInput("A0002", "1031-1035 CLINTON ST", "Hoboken", "NJ")
        matching = FakeGeocoder(
            {
                "1031 CLINTON ST": [candidate("1031 CLINTON ST", "Hoboken", "3401332250")],
                "1035 CLINTON ST": [candidate("1035 CLINTON ST", "Hoboken", "3401332250")],
            }
        )
        conflicting = FakeGeocoder(
            {
                "1031 CLINTON ST": [candidate("1031 CLINTON ST", "Hoboken", "3401332250")],
                "1035 CLINTON ST": [candidate("1035 CLINTON ST", "Jersey City", "3401336000")],
            }
        )

        self.assertEqual(resolve_addresses([address], matching)[0].status, "resolved")
        flagged = resolve_addresses([address], conflicting)[0]
        self.assertEqual(flagged.status, "needs_review")
        self.assertIn("range_endpoints_disagree", flagged.warnings)

    def test_wrong_house_number_does_not_become_a_resolution(self) -> None:
        address = AddressInput("A1", "12 OAK ST", "Newark", "NJ")
        geocoder = FakeGeocoder({"12 OAK ST": [candidate("14 OAK ST", "Newark", "3401351000")]})
        result = resolve_addresses([address], geocoder)[0]
        self.assertEqual(result.status, "needs_review")
        self.assertIsNone(result.legal_city)
        self.assertEqual(result.candidates[0].matched_address, "14 OAK ST, Newark, NJ")

    def test_safe_street_spelling_variants_keep_an_audit_warning(self) -> None:
        examples = (
            ("233 SECOND ST.", "233 2ND ST"),
            ("1311 SOUTH VAN NESS AV", "1311 S VAN NESS AVE"),
            ("4115 LINCOLN WY", "4115 LINCOLN WAY"),
            ("1636 M L KING JR WAY", "1636 MARTIN LUTHER KING JR WAY"),
            ("17106 CHATSWORTH ST APT 0001", "17106 CHATSWORTH ST"),
            ("151 North First St", "151 N 1ST ST"),
        )
        for input_street, matched_street in examples:
            with self.subTest(input_street=input_street):
                address = AddressInput("A1", input_street, "Newark", "NJ")
                result = resolve_addresses(
                    [address],
                    FakeGeocoder({input_street: [candidate(matched_street, "Newark", "3451000")]}),
                )[0]
                self.assertEqual(result.status, "resolved")
                self.assertEqual(result.attempts[0].outcome, "normalized_match")
                self.assertIn("street_normalized_match", result.warnings)

    def test_normalization_does_not_drop_directions_or_guess_streets(self) -> None:
        unsafe_pairs = (
            ("409 5TH ST", "409 N 5TH ST"),
            ("3784 E BROADWAY", "3784 BROADWAY"),
            ("4545 N 39TH ST", "4545 39TH ST"),
            ("65 NORFLOK ST", "65 NORFOLK ST"),
            ("368 RIVERWAY ST", "368 RIVERWAY"),
            ("12 OAK ST", "12 OAK RD"),
            ("14 OAK ST", "12 OAK ST"),
        )
        for input_street, matched_street in unsafe_pairs:
            with self.subTest(input_street=input_street):
                address = AddressInput("A1", input_street, "Newark", "NJ")
                result = resolve_addresses(
                    [address],
                    FakeGeocoder({input_street: [candidate(matched_street, "Newark", "3451000")]}),
                )[0]
                self.assertEqual(result.status, "needs_review")

    def test_range_with_one_normalized_endpoint_still_needs_review(self) -> None:
        address = AddressInput("A1", "233-235 SECOND ST.", "Jersey City", "NJ")
        result = resolve_addresses(
            [address],
            FakeGeocoder(
                {
                    "233 SECOND ST.": [candidate("233 2ND ST", "Jersey City", "3401336000")],
                    "235 SECOND ST.": [],
                }
            ),
        )[0]
        self.assertEqual(result.status, "needs_review")
        self.assertIn("street_normalized_match", result.warnings)

    def test_normalized_candidates_in_different_places_remain_ambiguous(self) -> None:
        address = AddressInput("A1", "233 SECOND ST", "Jersey City", "NJ")
        result = resolve_addresses(
            [address],
            FakeGeocoder(
                {
                    "233 SECOND ST": [
                        candidate("233 2ND ST", "Jersey City", "3401336000"),
                        candidate("233 2ND ST", "Hoboken", "3401332250"),
                    ],
                }
            ),
        )[0]
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.attempts[0].outcome, "ambiguous")

    def test_unexpected_legal_city_is_kept_and_flagged_for_review(self) -> None:
        address = AddressInput("A1", "12 OAK ST", "Newark", "NJ")
        geocoder = FakeGeocoder(
            {"12 OAK ST": [candidate("12 OAK ST", "Jersey City", "3401336000")]}
        )
        result = resolve_addresses([address], geocoder)[0]
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.legal_city, "Jersey City")
        self.assertIn("postal_city_differs_from_legal_city", result.warnings)
        with TemporaryDirectory() as directory:
            review_path = Path(directory) / "review.csv"
            write_review_csv([result], review_path)
            self.assertIn("A1,resolved", review_path.read_text(encoding="utf-8"))

    def test_alphanumeric_number_and_malformed_range_are_supported(self) -> None:
        lettered = AddressInput("A0306", "335A Harvard St", "Cambridge", "MA")
        lettered_geocoder = FakeGeocoder(
            {
                "335A Harvard St": [candidate("335A HARVARD ST", "Cambridge", "2511000", "MA")],
            }
        )
        self.assertEqual(resolve_addresses([lettered], lettered_geocoder)[0].status, "resolved")

        malformed_range = AddressInput("A0311", "38-38- SOMME ST", "Cambridge", "MA")
        range_geocoder = FakeGeocoder(
            {
                "38 SOMME ST": [candidate("38 SOMME ST", "Cambridge", "2511000", "MA")],
            }
        )
        result = resolve_addresses([malformed_range], range_geocoder)[0]
        self.assertEqual(result.status, "resolved")
        self.assertEqual(len(range_geocoder.queries), 1)

    def test_missing_house_number_does_not_call_geocoder(self) -> None:
        address = AddressInput("A0098", "WILLOWWOOD ST", "Dorchester", "MA")
        geocoder = FakeGeocoder({})
        result = resolve_addresses([address], geocoder)[0]
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.attempts[0].outcome, "invalid_input")
        self.assertEqual(geocoder.queries, [])

    def test_review_queue_explains_distinct_candidate_differences(self) -> None:
        address = AddressInput("A1", "585 5TH ST", "Newark", "NJ", "07107")
        variant = candidate("585 N 5TH ST", "Newark", "3451000")
        result = resolve_addresses([address], FakeGeocoder({"585 5TH ST": [variant]}))[0]
        self.assertEqual(result.status, "needs_review")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "review.csv"
            write_review_csv([result], path)
            with path.open(newline="", encoding="utf-8") as stream:
                row = next(csv.DictReader(stream))
        self.assertIn("street: 5TH ST → N 5TH ST", row["candidate_differences"])
        self.assertEqual(row["candidate_differences"].count("585 N 5TH ST [Newark, no ZIP]"), 1)

        no_candidate = resolve_addresses(
            [AddressInput("A2", "WILLOWWOOD ST", "Dorchester", "MA")], FakeGeocoder({})
        )[0]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "review.csv"
            write_review_csv([no_candidate], path)
            with path.open(newline="", encoding="utf-8") as stream:
                row = next(csv.DictReader(stream))
        self.assertEqual(row["candidate_differences"], "no candidate returned")

    def test_review_override_requires_current_input_and_source(self) -> None:
        address = AddressInput("A1", "12 OAK ST", "Newark", "NJ")
        result = resolve_addresses([address], FakeGeocoder({}))[0]
        override = ReviewOverride(
            "A1",
            "12 OAK ST",
            "Newark",
            "NJ",
            "NJ",
            "Newark",
            "https://example.gov/map",
            "Verified in city parcel map",
            "reviewer-1",
            "2026-10-04",
        )
        reviewed = apply_review_overrides([result], [override])[0]
        self.assertEqual(reviewed.status, "resolved")
        self.assertEqual(reviewed.legal_city, "Newark")
        self.assertEqual(reviewed.resolution_method, "review_override")
        self.assertEqual(reviewed.review_override.source_url, "https://example.gov/map")
        with self.assertRaisesRegex(ValueError, "stale street"):
            apply_review_overrides(
                [result],
                [
                    ReviewOverride(
                        "A1",
                        "14 OAK ST",
                        "Newark",
                        "NJ",
                        "NJ",
                        "Newark",
                        "https://example.gov/map",
                        "Verified",
                        "reviewer-1",
                        "2026-10-04",
                    )
                ],
            )
        with self.assertRaisesRegex(ValueError, "stale city or state"):
            apply_review_overrides([result], [replace(override, input_postal_city="Jersey City")])

    def test_review_override_csv_adapter(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "overrides.csv"
            path.write_text(
                "address_id,input_street_address,input_postal_city,input_state,legal_state,legal_city,source_url,reason,reviewer,reviewed_at\n"
                "A1,12 OAK ST,Newark,NJ,NJ,Newark,https://example.gov/map,Verified,reviewer-1,2026-10-04\n",
                encoding="utf-8",
            )
            overrides = read_review_overrides_csv(path)
            self.assertEqual(overrides[0].legal_city, "Newark")

    def test_census_response_parses_incorporated_place(self) -> None:
        raw = {
            "result": {
                "addressMatches": [
                    {
                        "matchedAddress": "12 OAK ST, NEWARK, NJ, 07102",
                        "coordinates": {"x": -74.1, "y": 40.7},
                        "addressComponents": {"state": "NJ", "zip": "07102"},
                        "geographies": {
                            "Incorporated Places": [{"BASENAME": "Newark", "GEOID": "3451000"}],
                            "Counties": [{"BASENAME": "Essex"}],
                        },
                    }
                ]
            }
        }
        response = CensusGeocoder()._parse(raw)
        self.assertEqual(response.candidates[0].city, "Newark")
        self.assertEqual(response.candidates[0].county, "Essex")
        self.assertEqual(response.candidates[0].city_geoid, "3451000")
        self.assertEqual(response.candidates[0].zip, "07102")

    def test_csv_and_json_are_outside_the_resolver(self) -> None:
        sample = Path(__file__).resolve().parents[2] / "data/sample_addresses.csv"
        addresses = read_in_from_csv(sample)
        self.assertEqual(len(addresses), 500)
        with TemporaryDirectory() as directory:
            results = resolve_addresses(addresses[:1], FakeGeocoder({}))
            path = Path(directory) / "resolved.json"
            write_to_internal_json(results, path)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsInstance(saved, list)
            self.assertEqual(saved[0]["address_id"], addresses[0].address_id)

    def test_uncached_offline_lookup_is_explicit_error(self) -> None:
        with TemporaryDirectory() as directory:
            offline = CachedGeocoder(Path(directory) / "cache.jsonl")
            with self.assertRaises(GeocoderError):
                offline.lookup(AddressQuery("12 OAK ST", "Newark", "NJ"))

    def test_cached_lookup_can_replay_offline_but_not_under_other_vintage(self) -> None:
        query = AddressQuery("12 OAK ST", "Newark", "NJ")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cache.jsonl"
            online = CachedGeocoder(
                path, FakeGeocoder({"12 OAK ST": [candidate("12 OAK ST", "Newark", "3451000")]})
            )
            self.assertEqual(len(online.lookup(query).candidates), 1)
            offline = CachedGeocoder(path)
            self.assertEqual(len(offline.lookup(query).candidates), 1)
            different_vintage = CachedGeocoder(path, vintage="Census2020_Current")
            with self.assertRaises(GeocoderError):
                different_vintage.lookup(query)

    def test_cli_reports_empty_offline_cache_as_failure(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            process = subprocess.run(
                [
                    sys.executable,
                    "scripts/resolve_addresses.py",
                    "--offline",
                    "--cache",
                    str(temp / "cache.jsonl"),
                    "--output",
                    str(temp / "resolved.json"),
                    "--review",
                    str(temp / "review.csv"),
                    "--zip-review",
                    str(temp / "zip_review.csv"),
                ],
                cwd=root,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("lookup failures", process.stderr)
            self.assertEqual(len(json.loads((temp / "resolved.json").read_text())), 500)

    def test_cli_can_apply_reviewed_result_without_a_cached_match(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            (temp / "addresses.csv").write_text(
                "address_id,street_address,postal_city,state,zip\nA1,12 OAK ST,Newark,NJ,\n",
                encoding="utf-8",
            )
            (temp / "overrides.csv").write_text(
                "address_id,input_street_address,input_postal_city,input_state,legal_state,legal_city,source_url,reason,reviewer,reviewed_at\n"
                "A1,12 OAK ST,Newark,NJ,NJ,Newark,https://example.gov/map,Verified,reviewer-1,2026-10-04\n",
                encoding="utf-8",
            )
            process = subprocess.run(
                [
                    sys.executable,
                    "scripts/resolve_addresses.py",
                    "--offline",
                    "--input",
                    str(temp / "addresses.csv"),
                    "--cache",
                    str(temp / "cache.jsonl"),
                    "--output",
                    str(temp / "resolved.json"),
                    "--review",
                    str(temp / "review.csv"),
                    "--zip-review",
                    str(temp / "zip_review.csv"),
                    "--overrides",
                    str(temp / "overrides.csv"),
                ],
                cwd=root,
                text=True,
                capture_output=True,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            saved = json.loads((temp / "resolved.json").read_text())
            self.assertEqual(saved[0]["resolution_method"], "review_override")
            self.assertEqual(len((temp / "review.csv").read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
