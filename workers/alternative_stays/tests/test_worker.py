import unittest

from workers.alternative_stays.worker import handle

BASE = {
    "type": "stay.search.v1",
    "request_id": "t-001",
    "destination": "New York City",
    "check_in": "2027-01-20",
    "check_out": "2027-01-23",
    "guests": 2,
    "required_amenities": ["wifi"],
    "preferences": ["quiet"],
}


class WorkerTests(unittest.TestCase):
    def test_offline_and_user_quote_not_verified(self):
        result = handle({**BASE, "provided_listings": [{
            "name": "Example", "description": "Wifi and kitchen", "total_usd": 500
        }]})
        self.assertEqual(result["type"], "stay.results.v1")
        self.assertEqual(len(result["offers"]), 1)
        offer = result["offers"][0]
        self.assertEqual(offer["price"]["total_usd"], 500)
        self.assertEqual(offer["price"]["status"], "user_reported_unverified")
        self.assertEqual(offer["availability"], "unknown")
        self.assertFalse(offer["price"]["fees_complete"])
        self.assertFalse(offer["evidence"][0]["verified"])

    def test_search_and_sie_mocked(self):
        def search(_):
            return [{"name": "Sample", "source_url": "https://example.org/property", "description": "Entire studio, wifi", "source_type": "keenable_indexed_discovery"}]

        def classifier(_):
            return [{"label": "wireless internet mentioned", "score": 0.8}]

        result = handle(BASE, discover=search, classify=classifier)
        self.assertEqual(result["meta"]["search_calls"], 1)
        self.assertEqual(result["meta"]["sie_calls"], 1)
        self.assertEqual(result["offers"][0]["price"]["total_usd"], None)
        self.assertEqual(result["offers"][0]["signals"]["status"], "model_inference_not_verified")

    def test_invalid_dates(self):
        with self.assertRaises(ValueError):
            handle({**BASE, "check_out": "2027-01-19"})

    def test_verify_does_not_forge_result(self):
        result = handle({"type": "stay.verify.v1", "request_id": "t-001", "offer_id": "abc"})
        self.assertEqual(result["status"], "unverified")

    def test_discovery_failure_is_visible(self):
        def fail(_):
            raise TimeoutError("backend unavailable")
        result = handle(BASE, discover=fail)
        self.assertEqual(len(result["offers"]), 0)
        self.assertTrue(any("backend unavailable" in s for s in result["meta"]["warnings"]))

    def test_duplicate_candidates_are_merged_by_exact_url(self):
        same = {"name": "A", "source_url": "https://example.org/a", "description": "One"}
        result = handle({**BASE, "provided_listings": [same, same]})
        self.assertEqual(len(result["offers"]), 1)


if __name__ == "__main__":
    unittest.main()
