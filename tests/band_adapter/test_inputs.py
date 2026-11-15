"""extract_inputs: what the adapter hands the graph, turned into (sender, content) records."""

import json

from plan_my_stay.band_adapter.inputs import IncomingInput, extract_inputs, strip_prose_decoration


def _block(*records: dict, count: int | None = None) -> str:
    lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    return f"## Current input messages ({count or len(records)})\nThese are the messages delivered to you.\n{lines}"


def test_sdk_9_2_block_with_one_record():
    text = _block({"message_id": "m1", "sender_id": "s1", "sender_name": "Plan My Stay Stay Finder",
                   "sender_type": "Agent", "timestamp": "2026-11-14T10:00:00+00:00", "content": "@mario/routing {\"a\": 1}"})
    assert extract_inputs(text) == (
        IncomingInput(sender_name="Plan My Stay Stay Finder", content='@mario/routing {"a": 1}',
                      message_id="m1", sender_id="s1", sender_type="Agent"),
    )


def test_sdk_9_2_block_with_several_records_keeps_order():
    text = _block({"sender_name": "A", "content": "one"}, {"sender_name": "B", "content": "two"})
    assert [(i.sender_name, i.content) for i in extract_inputs(text)] == [("A", "one"), ("B", "two")]


def test_a_missing_sender_name_falls_back_to_the_sender_id():
    text = _block({"sender_id": "uuid-1", "sender_name": None, "content": "hi"})
    assert extract_inputs(text)[0].sender_name == "uuid-1"


def test_content_with_newlines_survives_because_records_are_one_json_line_each():
    text = _block({"sender_name": "Mario", "content": "line one\nline two"})
    assert extract_inputs(text)[0].content == "line one\nline two"


def test_lines_that_are_not_records_are_ignored():
    text = _block({"sender_name": "Mario", "content": "hi"}) + "\n{not json\n[1, 2]\n{\"no_content\": 1}"
    assert [i.content for i in extract_inputs(text)] == ["hi"]


def test_quickstart_bracket_form_is_still_understood():
    assert extract_inputs("[Mario]: @concierge plan my trip") == (
        IncomingInput(sender_name="Mario", content="@concierge plan my trip"),
    )


def test_bracket_form_keeps_multiline_content():
    assert extract_inputs("[Mario]: a\nb")[0].content == "a\nb"


def test_unrecognised_text_yields_no_inputs():
    assert extract_inputs("just some text") == ()
    assert extract_inputs("") == ()


def test_strip_prose_decoration_removes_prefix_and_leading_handles_only():
    assert strip_prose_decoration("[Mario]: @mario/concierge @other hello @someone") == "hello @someone"
    assert strip_prose_decoration("@concierge   hi there") == "hi there"
    assert strip_prose_decoration("no decoration") == "no decoration"
    assert strip_prose_decoration("@concierge") == ""
