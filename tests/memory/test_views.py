"""Turning parsed records into what MemoryPort returns: the profile, the latest trip state, fresh city facts."""

import datetime as dt
import logging

import pytest

from plan_my_stay.demo import demo_state
from plan_my_stay.enums import Mode
from plan_my_stay.memory import text, views
from plan_my_stay.models import Profile
from plan_my_stay.timeutil import TRIP_TZ

T0 = dt.datetime(2026, 11, 14, 16, 0, tzinfo=TRIP_TZ)


def _records(*specs: tuple[str, str, object, int], user: str = "u1") -> tuple[text.PreferenceRecord, ...]:
    """(user_override or "", key, value, minutes after T0) -> records, in the order given."""
    lines = [
        text.preference_line(
            agent="concierge", user_id=who or user, trip_id="t", key=key, value=value, evidence="e", at=T0 + dt.timedelta(minutes=minutes)
        )
        for who, key, value, minutes in specs
    ]
    return text.parse_preferences(lines)


def test_no_records_give_an_empty_profile() -> None:
    assert views.build_profile((), "u1") == Profile()


def test_every_known_field_is_filled_and_typed() -> None:
    records = _records(
        ("", "diet", ["vegetarian", "gluten-free"], 0),
        ("", "cuisines", "Thai", 0),  # a bare string is a one-item list
        ("", "gym_types", ["hiit", "hiit", "yoga"], 0),  # repeats collapse
        ("", "workout_times", ["06:30", "18:00"], 0),
        ("", "stay_preferences", ["quiet street"], 0),
        ("", "price_comfort", "splurge on dinner", 0),
        ("", "transport_mode", "transit", 0),
    )

    profile = views.build_profile(records, "u1")

    assert profile.diet == ("vegetarian", "gluten-free")
    assert profile.cuisines == ("Thai",)
    assert profile.gym_types == ("hiit", "yoga")
    assert profile.workout_times == ("06:30", "18:00")
    assert profile.stay_preferences == ("quiet street",)
    assert profile.price_comfort == "splurge on dinner"
    assert profile.transport_mode is Mode.TRANSIT


def test_other_users_and_unknown_keys() -> None:
    records = _records(("", "diet", ["vegan"], 0), ("u2", "diet", ["omnivore"], 1), ("", "favorite_bar", "Dead Rabbit", 2))

    profile = views.build_profile(records, "u1")

    assert profile.diet == ("vegan",)
    assert profile.model_extra == {"favorite_bar": "Dead Rabbit"}
    assert views.build_profile(records, "u2").diet == ("omnivore",)
    assert views.build_profile(records, "u3") == Profile()


def test_the_newest_record_per_key_wins_whatever_order_recall_returns_them_in() -> None:
    records = _records(("", "diet", ["vegan"], 30), ("", "diet", ["vegetarian"], 10), ("", "diet", ["pescatarian"], 20))

    assert views.build_profile(records, "u1").diet == ("vegan",)


def test_equal_times_fall_back_to_the_later_record() -> None:
    records = _records(("", "diet", ["first"], 5), ("", "diet", ["second"], 5))

    assert views.build_profile(records, "u1").diet == ("second",)


def test_undated_records_lose_to_dated_ones() -> None:
    undated = text.parse_preferences(['agent=x user=u1 key=diet value=["old"]'])
    dated = _records(("", "diet", ["new"], 0))

    assert views.build_profile((*dated, *undated), "u1").diet == ("new",)


def test_an_empty_list_is_a_real_value_that_clears_the_field() -> None:
    records = _records(("", "diet", ["vegan"], 0), ("", "diet", [], 1))

    assert views.build_profile(records, "u1").diet == ()


def test_unusable_values_for_known_fields_are_dropped_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    records = _records(
        ("", "transport_mode", "teleport", 0),
        ("", "workout_times", ["7am", "07:30", 730], 0),
        ("", "price_comfort", ["not", "a", "string"], 0),
        ("", "diet", {"a": 1}, 0),
    )

    with caplog.at_level(logging.WARNING, logger="plan_my_stay.memory"):
        profile = views.build_profile(records, "u1")

    assert profile.transport_mode is None
    assert profile.workout_times == ("07:30",)
    assert profile.price_comfort is None
    assert profile.diet == ()
    warned = " ".join(r.getMessage() for r in caplog.records)
    assert "transport_mode" in warned and "workout_times" in warned and "price_comfort" in warned and "diet" in warned


def test_numbers_in_a_string_list_become_strings() -> None:
    records = _records(("", "cuisines", ["Thai", 7], 0))

    assert views.build_profile(records, "u1").cuisines == ("Thai", "7")


# --- trip state ----------------------------------------------------------------------------


def _state_records(*states):
    lines = [text.trip_state_line(agent="concierge", state=s, at=T0 + dt.timedelta(minutes=i)) for i, s in enumerate(states)]
    return text.parse_trip_states(lines)


def test_the_highest_state_version_wins_then_the_latest_copy() -> None:
    v0 = demo_state()
    v1, v2 = v0.bump(), v0.bump().bump()
    records = _state_records(v2, v0, v1, v2.bump(round=1), v2.bump(round=2))  # two copies of version 3

    loaded = views.latest_trip_state(records, user_id=v0.user_id, trip_id=v0.trip_id)

    assert loaded is not None and loaded.state_version == 3 and loaded.round == 2


def test_trip_state_is_filtered_by_user_and_trip() -> None:
    state = demo_state()
    records = _state_records(state)

    assert views.latest_trip_state(records, user_id=state.user_id, trip_id=state.trip_id) == state
    assert views.latest_trip_state(records, user_id="other", trip_id=state.trip_id) is None
    assert views.latest_trip_state(records, user_id=state.user_id, trip_id="other-trip") is None
    assert views.latest_trip_state((), user_id="u", trip_id="t") is None


def test_a_line_whose_labels_disagree_with_its_state_is_not_trusted() -> None:
    state = demo_state()
    line = text.trip_state_line(agent="concierge", state=state, at=T0).replace(f"user={state.user_id}", "user=someone-else", 1)
    records = text.parse_trip_states([line])

    assert views.latest_trip_state(records, user_id="someone-else", trip_id=state.trip_id) is None
    assert views.latest_trip_state(records, user_id=state.user_id, trip_id=state.trip_id) is None


# --- city facts ----------------------------------------------------------------------------


def _fact(fact: str, hours_ago: float, *, city: str = "New York, NY", url: str = "https://a.test/x") -> text.CityFactRecord:
    line = text.city_fact_line(agent="gym", city=city, fact=fact, source_url=url, fetched_at=T0 - dt.timedelta(hours=hours_ago))
    (record,) = text.parse_city_facts([line])
    return record


def _select(records, *, query: str = "gym", city: str = "New York, NY", now: dt.datetime = T0, limit: int = 15):
    return views.select_city_facts(records, city=city, query=query, now=now, price_ttl=views.DEFAULT_PRICE_FACT_TTL, limit=limit)


@pytest.mark.parametrize(
    "fact, is_price",
    [
        ("Day pass is $35", True),
        ("Entrees cost 18 USD", True),
        ("Rooms are priced from 210 per night", True),
        ("Resort fee of 25", True),
        ("Price level: 2 of 4", True),
        ("Rates start at €90", True),
        ("Open 06:00-22:00", False),
        ("Quiet street, near the High Line", False),
        ("The gym has a sauna", False),
    ],
)
def test_price_facts_are_recognised_from_their_text(fact: str, is_price: bool) -> None:
    assert views.is_price_fact(fact) is is_price


def test_price_facts_older_than_the_ttl_are_dropped_and_other_facts_stay() -> None:
    records = [_fact("Day pass is $35 gym", 25), _fact("Day pass is $40 gym", 23), _fact("Gym open 06:00-22:00", 24 * 30)]

    kept = _select(records)

    assert [r.fact for r in kept] == ["Day pass is $40 gym", "Gym open 06:00-22:00"]


def test_facts_are_newest_first_and_only_for_the_asked_city() -> None:
    records = [
        _fact("gym a", 5),
        _fact("gym b", 1),
        _fact("gym c in boston", 0, city="Boston, MA"),
        _fact("gym d", 3),
    ]

    assert [r.fact for r in _select(records)] == ["gym b", "gym d", "gym a"]
    assert [r.fact for r in _select(records, city=" NEW  york, ny")] == ["gym b", "gym d", "gym a"]


def test_the_same_fact_from_the_same_source_is_listed_once_with_its_newest_time() -> None:
    records = [_fact("gym open late", 5), _fact("gym open late", 1), _fact("gym open late", 2, url="https://other.test")]

    kept = _select(records)

    assert [(r.fact, r.source_url, (T0 - r.fetched_at).total_seconds() / 3600) for r in kept] == [
        ("gym open late", "https://a.test/x", 1.0),
        ("gym open late", "https://other.test", 2.0),
    ]


def test_relevant_facts_are_preferred_but_a_city_is_never_left_empty_by_the_query() -> None:
    records = [_fact("Chelsea Fitness opens at 06:00", 1), _fact("The High Line is a park", 2)]

    assert [r.fact for r in _select(records, query="chelsea fitness hours")] == ["Chelsea Fitness opens at 06:00"]
    assert [r.fact for r in _select(records, query="completely unrelated words")] == [
        "Chelsea Fitness opens at 06:00",
        "The High Line is a park",
    ]
    assert len(_select(records, query="")) == 2


def test_the_limit_caps_the_answer() -> None:
    records = [_fact(f"gym number {i}", i) for i in range(30)]

    assert len(_select(records)) == 15
    assert len(_select(records, limit=3)) == 3
