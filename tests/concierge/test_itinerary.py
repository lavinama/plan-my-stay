"""The itinerary: a deterministic template that always works, and an optional frontier-model polish that may not add facts."""

import pytest

from plan_my_stay import demo
from plan_my_stay.concierge.itinerary import (
    describe_conflict,
    extract_minutes,
    extract_prices,
    extract_times,
    render_itinerary,
    render_template,
)
from plan_my_stay.concierge.llm import FakeLLM, LLMError
from plan_my_stay.enums import VerdictStatus
from plan_my_stay.messages.payloads import VerdictPayload
from plan_my_stay.models import Conflict, CostBasis

ADDRESSES = demo.MEETING_ADDRESSES
STATE = demo.demo_state_with_plan()
WARN_CONFLICT = Conflict(
    rule="fee_basis_unknown",
    item_id="stay-chelsea-1",
    detail="A 15% fee reserve was added to the stay total.",
    suggestion=None,
)
WARN_VERDICT = VerdictPayload(status=VerdictStatus.WARN, plan_hash=STATE.plan_hash(), conflicts=(WARN_CONFLICT,))
CLEAR_VERDICT = VerdictPayload(status=VerdictStatus.CLEAR, plan_hash=STATE.plan_hash())


def template(state=STATE, verdict=CLEAR_VERDICT, warnings=(), notes=()) -> str:
    return render_template(state, verdict, meeting_addresses=ADDRESSES, warnings=warnings, notes=notes)


# --- fact extraction ---------------------------------------------------------------------------


def test_extract_prices_reads_symbols_and_codes_as_numbers():
    assert extract_prices("Total $1,000.00, a pass for USD 35 and EUR 12.5, also €7") == {1000.0, 35.0, 12.5, 7.0}
    assert extract_prices("no money here, only 17 min") == set()


def test_extract_times_normalizes_clock_times_and_keeps_am_pm_forms_distinct():
    assert extract_times("at 7:00, 18:30 and 09:05") == {"07:00", "18:30", "09:05"}
    assert extract_times("around 9 pm or 6:30 PM") == {"9pm", "06:30"}  # am/pm forms never match a 24-hour template


def test_extract_minutes_reads_durations():
    assert extract_minutes("transit 25 min, then walking 4.5 minutes") == {25.0, 4.5}


# --- the template ------------------------------------------------------------------------------


def test_the_template_shows_every_meeting_with_its_address_and_time():
    text = template()

    for address in ADDRESSES.values():
        assert address in text
    for meeting in ("Meeting A", "Meeting B", "Meeting C"):
        assert meeting in text
    for time in ("11:00", "12:00", "16:00", "17:00", "10:00"):
        assert time in text


def test_the_template_shows_the_workout_and_the_meal_with_address_time_and_price():
    text = template()

    assert "Example HIIT Studio" in text and "1 Example Plaza" in text
    assert "17:30" in text and "18:15" in text
    assert "Urban Vegan Kitchen" in text and "265 W 23rd St" in text
    assert "18:30" in text and "19:45" in text
    assert "$35.00" in text  # the day pass; the dinner's price is unknown and is not invented


def test_the_template_shows_the_stay_with_total_price_budget_and_fee_basis_note():
    text = template()

    assert "Quiet Chelsea studio" in text
    assert "$640.00" in text and "2 nights" in text
    assert "$1,000.00" in text  # the budget
    assert "fees" in text.lower() and "not confirmed" in text.lower()  # card_total_fees_unknown


def test_a_stay_total_that_includes_fees_has_no_fee_warning_note():
    state = demo.demo_state_with_plan(stay_basis=CostBasis.TOTAL_INCL_FEES)

    assert "not confirmed" not in template(state).lower()


def test_the_template_shows_transport_mode_and_minutes_for_every_leg():
    text = template()

    for leg in STATE.legs:
        assert f"{leg.chosen_mode.value} {leg.minutes_by_mode[leg.chosen_mode]:g} min" in text
        assert f"{leg.depart_at:%H:%M}" in text


def test_days_come_in_order_and_events_in_time_order():
    text = template()

    assert (
        text.index("\nTue 17 Nov\n") < text.index("\nWed 18 Nov\n") < text.index("\nThu 19 Nov\n")
    )  # the day headings
    assert (
        text.index("Meeting A")
        < text.index("Meeting B")
        < text.index("Example HIIT Studio")
        < text.index("Urban Vegan Kitchen")
    )
    assert text.index("Urban Vegan Kitchen") < text.index("Meeting C")


def test_every_warning_is_shown():
    text = template(warnings=("Gym hours could not be checked.", "Budget was widened to $1,250.00."))

    assert "Gym hours could not be checked." in text
    assert "Budget was widened to $1,250.00." in text


def test_open_conflicts_are_shown_as_warnings():
    conflict = Conflict(
        rule="open_during_slot",
        item_id="rest-urban-vegan-kitchen",
        detail="Closes at 18:00 on Tue.",
        suggestion="Pick a later place.",
    )
    state = STATE.bump(open_conflicts=(conflict,))
    block = VerdictPayload(status=VerdictStatus.BLOCK, conflicts=(conflict,))

    text = template(state, block)

    assert "Closes at 18:00 on Tue." in text
    assert text.count("Closes at 18:00 on Tue.") == 1  # in the state and the verdict, shown once
    assert "Pick a later place." in text


def test_a_warn_verdicts_conflicts_are_shown():
    assert "A 15% fee reserve was added to the stay total." in template(verdict=WARN_VERDICT)


def test_a_clean_plan_has_no_heads_up_section():
    assert "Heads-up" not in template()


def test_notes_such_as_the_stay_trade_off_are_shown():
    assert "TriBeCa loft cuts the commute." in template(notes=("TriBeCa loft cuts the commute.",))


def test_a_state_without_a_stay_still_renders():
    state = demo.demo_state()

    text = template(state, None)

    assert "No stay" in text and "Meeting A" in text


def test_a_missing_meeting_address_does_not_break_the_template():
    text = render_template(STATE, None, meeting_addresses={}, warnings=(), notes=())

    assert "Meeting A" in text


def test_a_leg_with_no_route_says_so():
    from plan_my_stay.models import Leg

    broken = Leg(from_id="A", to_id="B", depart_at=demo.at(17, 12), minutes_by_mode={"walking": None}, chosen_mode=None)
    state = STATE.bump(legs=(broken,))

    assert "no route" in template(state).lower()


def test_describe_conflict_names_the_rule_in_plain_words_and_keeps_the_detail_and_suggestion():
    text = describe_conflict(
        Conflict(rule="travel_time", item_id="B", detail="Only 5 min between A and B.", suggestion="Start later.")
    )

    assert "Only 5 min between A and B." in text and "Start later." in text
    assert "travel_time" not in text  # the user sees words, not rule names


def test_describe_conflict_handles_an_unknown_rule():
    assert "Something odd." in describe_conflict(Conflict(rule="brand_new_rule", item_id="x", detail="Something odd."))


# --- the polish --------------------------------------------------------------------------------


def _polish(response, **kwargs) -> tuple[str, FakeLLM]:
    llm = FakeLLM([response] if not isinstance(response, list) else response)
    text = render_itinerary(
        STATE, WARN_VERDICT, meeting_addresses=ADDRESSES, warnings=kwargs.pop("warnings", ()), llm=llm, **kwargs
    )
    return text, llm


def test_without_an_llm_the_result_is_the_template():
    text = render_itinerary(STATE, WARN_VERDICT, meeting_addresses=ADDRESSES, warnings=("W1",), llm=None)

    assert text == template(verdict=WARN_VERDICT, warnings=("W1",))


def test_a_faithful_polish_is_used():
    base = template(verdict=WARN_VERDICT, warnings=("W1 matters.",))
    polished = f"Have a great trip!\n\n{base}\n\nEnjoy."

    text, llm = _polish(polished, warnings=("W1 matters.",))

    assert text == polished
    assert base in llm.calls[0].prompt  # the model is given the template's facts
    assert llm.calls[0].json_schema is None


def test_a_polish_that_adds_a_price_falls_back_to_the_template():
    base = template(verdict=WARN_VERDICT)

    text, _ = _polish(base + "\nTip: the dinner usually costs $42.00.")

    assert text == base


def test_a_polish_that_adds_a_time_falls_back_to_the_template():
    base = template(verdict=WARN_VERDICT)

    assert _polish(base + "\nDoors open at 06:15.")[0] == base
    assert _polish(base + "\nDoors open at 6 am.")[0] == base


def test_a_polish_that_adds_a_travel_time_falls_back_to_the_template():
    base = template(verdict=WARN_VERDICT)

    assert _polish(base + "\nThe walk takes 33 min.")[0] == base


def test_prices_may_be_written_without_cents():
    base = template(verdict=WARN_VERDICT)

    assert _polish(base.replace("$640.00", "$640"))[0] != base


def test_a_polish_that_drops_a_warning_or_an_address_falls_back_to_the_template():
    base = template(verdict=WARN_VERDICT, warnings=("W1 matters.",))

    dropped_warning = base.replace("W1 matters.", "")
    assert _polish(dropped_warning, warnings=("W1 matters.",))[0] == base
    dropped_address = base.replace(ADDRESSES["A"], "somewhere downtown")
    assert _polish(dropped_address, warnings=("W1 matters.",))[0] == base


@pytest.mark.parametrize("failure", [LLMError("model is down"), RuntimeError("boom"), "", "   "])
def test_an_llm_failure_or_empty_answer_falls_back_to_the_template(failure):
    text, _ = _polish(failure)

    assert text == template(verdict=WARN_VERDICT)
