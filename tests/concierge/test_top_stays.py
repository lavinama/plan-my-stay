"""The reply when the Concierge talks only to the Stay agent: the top three stays and the commute behind each."""

import pytest

from plan_my_stay import demo
from plan_my_stay.concierge.ranking import keep_stay_order
from plan_my_stay.concierge.top_stays import render_top_stays
from plan_my_stay.models import CostBasis, Meeting, StayOption

MEETINGS = tuple(
    Meeting(id=a.id, address=demo.MEETING_ADDRESSES[a.id], start=a.start, end=a.end, counts_for_stay=a.counts_for_stay)
    for a in demo.demo_anchors()
)


def _stay(i: int, commute: float | None, price: float, *, basis: str = CostBasis.TOTAL_INCL_FEES, zone: str = "z1") -> StayOption:
    return StayOption(rank=i, deciding_factor="commute", stay_id=f"stay-{i}", zone_id=zone, name=f"Hotel {i}", total_price=price,
                      cost_basis=basis, average_commute_minutes=commute)


def _render(stays, *, notes=(), warnings=(), reserve=0.15):
    ranked = keep_stay_order(stays, anchor_ids=("B", "C"))
    state = demo.demo_state().bump(stay=stays[0], ranked_stays=ranked) if stays else demo.demo_state()
    return render_top_stays(state, MEETINGS, notes=notes, warnings=warnings, reserve=reserve)


THREE = [_stay(1, 11.5, 640), _stay(2, 12.5, 780, zone="z2"), _stay(3, 13.0, 905, zone="z3")]


def test_the_header_names_the_trip_and_the_budget():
    text = _render(THREE)

    assert text.startswith("Top 3 stays: New York, NY, Tue 17 Nov to Thu 19 Nov (2 nights, 1 guest)")
    assert "Budget: $1,000.00 for the stay plus transport" in text


def test_each_stay_is_listed_in_order_with_its_zone_price_and_average_commute():
    lines = [line for line in _render(THREE).splitlines() if line.lstrip().startswith(("1.", "2.", "3."))]

    assert len(lines) == 3
    assert "1. Hotel 1 (Chelsea): $640.00 total" in lines[0] and "Average commute 11.5 min" in lines[0]
    assert "2. Hotel 2 (TriBeCa): $780.00 total" in lines[1] and "Average commute 12.5 min" in lines[1]
    assert "3. Hotel 3 (Financial District): $905.00 total" in lines[2] and "Average commute 13.0 min" in lines[2]


def test_only_the_first_is_recommended():
    lines = [line for line in _render(THREE).splitlines() if line.lstrip().startswith(("1.", "2.", "3."))]

    assert "Recommended" in lines[0] and "Recommended" not in lines[1] and "Recommended" not in lines[2]


def test_an_unconfirmed_fee_basis_shows_the_total_with_the_reserve_and_a_confirmed_one_does_not():
    text = _render([_stay(1, 11.5, 640, basis=CostBasis.CARD_TOTAL_FEES_UNKNOWN), _stay(2, 12.5, 700)])
    first, second = [line for line in text.splitlines() if line.lstrip().startswith(("1.", "2."))]

    assert "fees not confirmed" in first and "$736.00" in first and "15%" in first
    assert "fees" not in second


def test_the_meetings_that_counted_are_named_and_one_before_check_in_is_said_not_to_count():
    text = _render(THREE)

    assert "B (368 9th Ave, New York, NY 10001)" in text and "C (524 Broadway, New York, NY 10012)" in text
    assert "A (199 Water St, New York, NY 10038)" in text.split("Not counted")[1]  # before check-in


def test_notes_and_heads_ups_appear_only_when_there_are_some():
    plain = _render(THREE)
    noted = _render(THREE, notes=["Hotel 0 ranked first but would cost $1,035.00, over your budget, so it was set aside."],
                    warnings=["No stay fit your $1,000.00 budget, so the budget was widened to $1,250.00."])

    assert "Notes" not in plain and "Heads-up" not in plain
    assert "- Hotel 0 ranked first but would cost" in noted.split("Notes")[1]
    assert "- No stay fit your $1,000.00 budget" in noted.split("Heads-up")[1]


@pytest.mark.parametrize("count, heading", [(1, "Best stay: "), (2, "Top 2 stays: ")])
def test_fewer_than_three_stays_say_so(count, heading):
    assert _render(THREE[:count]).startswith(heading)


def test_a_stay_with_no_commute_data_says_so():
    text = _render([_stay(1, 11.5, 640), _stay(2, None, 700)])

    assert "Hotel 2" in text and "Average commute unknown" in text


def test_it_says_what_this_answer_is_and_is_not():
    text = _render(THREE)

    assert "Prices are totals for 2 nights" in text
    assert "not planned day by day" in text  # no itinerary or Verifier check in this mode


def test_there_must_be_a_stay_to_show():
    with pytest.raises(ValueError):
        _render([])
