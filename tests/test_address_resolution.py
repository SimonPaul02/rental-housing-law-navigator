"""Boundary and missing-data checks using a fake geocoder, with no network."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from address_resolution.census import CachedGeocoder, CensusGeocoder
from address_resolution.io import read_in_from_csv, write_to_internal_json
from address_resolution.models import AddressInput, AddressQuery, GeocodeCandidate, GeocodeResponse
from address_resolution.ports import GeocoderError
from address_resolution.resolver import resolve_addresses


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
        geocoder = FakeGeocoder({"876 S 14TH ST": [candidate("876 S 14TH ST", "Newark", "3401351000")]})

        result = resolve_addresses([address], geocoder)[0]

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.legal_city, "Newark")
        self.assertIn("suspicious_input_zip", result.warnings)
        self.assertEqual(geocoder.queries[0].zip, "")

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
        matching = FakeGeocoder({
            "1031 CLINTON ST": [candidate("1031 CLINTON ST", "Hoboken", "3401332250")],
            "1035 CLINTON ST": [candidate("1035 CLINTON ST", "Hoboken", "3401332250")],
        })
        conflicting = FakeGeocoder({
            "1031 CLINTON ST": [candidate("1031 CLINTON ST", "Hoboken", "3401332250")],
            "1035 CLINTON ST": [candidate("1035 CLINTON ST", "Jersey City", "3401336000")],
        })

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

    def test_unexpected_legal_city_is_reviewed(self) -> None:
        address = AddressInput("A1", "12 OAK ST", "Newark", "NJ")
        geocoder = FakeGeocoder({"12 OAK ST": [candidate("12 OAK ST", "Jersey City", "3401336000")]})
        result = resolve_addresses([address], geocoder)[0]
        self.assertEqual(result.status, "needs_review")
        self.assertIn("legal_city_differs_from_search_hint", result.warnings)

    def test_census_response_parses_incorporated_place(self) -> None:
        raw = {"result": {"addressMatches": [{
            "matchedAddress": "12 OAK ST, NEWARK, NJ, 07102",
            "coordinates": {"x": -74.1, "y": 40.7},
            "addressComponents": {"state": "NJ"},
            "geographies": {
                "Incorporated Places": [{"BASENAME": "Newark", "GEOID": "3451000"}],
                "Counties": [{"BASENAME": "Essex"}],
            },
        }]}}
        response = CensusGeocoder()._parse(raw)
        self.assertEqual(response.candidates[0].city, "Newark")
        self.assertEqual(response.candidates[0].county, "Essex")
        self.assertEqual(response.candidates[0].city_geoid, "3451000")

    def test_csv_and_json_are_outside_the_resolver(self) -> None:
        sample = Path(__file__).resolve().parents[1] / "data/sample_addresses.csv"
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
            online = CachedGeocoder(path, FakeGeocoder({
                "12 OAK ST": [candidate("12 OAK ST", "Newark", "3451000")]
            }))
            self.assertEqual(len(online.lookup(query).candidates), 1)
            offline = CachedGeocoder(path)
            self.assertEqual(len(offline.lookup(query).candidates), 1)
            different_vintage = CachedGeocoder(path, vintage="Census2020_Current")
            with self.assertRaises(GeocoderError):
                different_vintage.lookup(query)


if __name__ == "__main__":
    unittest.main()
