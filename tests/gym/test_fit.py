from datetime import datetime
from zoneinfo import ZoneInfo

from workers.gym.fit import build_item, rank_candidates, score_candidate
from workers.gym.slots import TimeRange

NY = ZoneInfo("America/New_York")
WINDOW = TimeRange(
    start=datetime(2026, 11, 17, 17, 30, tzinfo=NY),
    end=datetime(2026, 11, 17, 18, 15, tzinfo=NY),
)
PROFILE = {"gym_type": ["HIIT"]}


def _candidate(**overrides):
    base = {
        "name": "Test Gym",
        "address": {"street": "1 Test St", "city": "New York", "zip": "10001"},
        "lat": 40.74,
        "lng": -73.99,
        "gym_type": ["HIIT"],
        "hours": {"Tue": "06:00-22:00"},
        "source": {"url": "https://example.test/gym", "fetched_at": "2026-11-14T10:00:00-05:00"},
    }
    base.update(overrides)
    return base


def test_matching_gym_type_is_eligible():
    fit = score_candidate(_candidate(gym_type=["HIIT", "Strength"]), PROFILE, WINDOW)
    assert fit["eligible"] is True
    assert fit["missing"] == []


def test_non_matching_gym_type_is_excluded():
    fit = score_candidate(_candidate(gym_type=["Yoga"]), PROFILE, WINDOW)
    assert fit["eligible"] is False
    assert "Yoga" in fit["reason"] or "yoga" in fit["reason"].lower()


def test_missing_gym_type_is_eligible_but_flagged():
    fit = score_candidate(_candidate(gym_type=None), PROFILE, WINDOW)
    assert fit["eligible"] is True
    assert "gym_type" in fit["missing"]


def test_hours_that_do_not_cover_window_are_excluded():
    fit = score_candidate(_candidate(hours={"Tue": "19:00-22:00"}), PROFILE, WINDOW)
    assert fit["eligible"] is False


def test_list_valued_hours_are_supported():
    # docs/PLAN.md: hours values can be "several periods as a list" (e.g.
    # a gym closed midday). The window (17:30-18:15) falls in the second
    # period.
    fit = score_candidate(
        _candidate(hours={"Tue": ["06:00-10:00", "17:00-21:00"]}), PROFILE, WINDOW
    )
    assert fit["eligible"] is True
    assert fit["missing"] == []


def test_missing_hours_are_eligible_but_flagged():
    fit = score_candidate(_candidate(hours=None), PROFILE, WINDOW)
    assert fit["eligible"] is True
    assert "hours" in fit["missing"]


def test_ranking_puts_confirmed_fit_ahead_of_missing_fact():
    confirmed = _candidate(name="Confirmed Gym")
    missing_hours = _candidate(name="Unconfirmed Gym", hours=None)
    result = rank_candidates([missing_hours, confirmed], PROFILE, WINDOW)

    assert [c["name"] for c in result["ranked"]] == ["Confirmed Gym", "Unconfirmed Gym"]
    assert result["excluded"] == []


def test_ranking_drops_ineligible_candidates():
    confirmed = _candidate(name="Confirmed Gym")
    wrong_type = _candidate(name="Wrong Type Gym", gym_type=["Yoga"])
    result = rank_candidates([confirmed, wrong_type], PROFILE, WINDOW)

    assert [c["name"] for c in result["ranked"]] == ["Confirmed Gym"]
    assert [c["name"] for c in result["excluded"]] == ["Wrong Type Gym"]


def test_build_item_fixture_reason_mentions_cached():
    candidate = _candidate()
    candidate["_fit"] = score_candidate(candidate, PROFILE, WINDOW)
    item = build_item(
        candidate,
        item_id="gym-test-gym-2026-11-17",
        slot_id="gym-2026-11-17",
        date="2026-11-17",
        when=WINDOW,
        model="fixture",
    )
    assert "cached" in item["fit"]["reason"].lower()
    assert item["location_precision"] == "exact"
    assert item["reader"]["model"] == "fixture"
    assert item["reader"]["escalated"] is False
