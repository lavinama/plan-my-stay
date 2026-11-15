"""The in-process room: a stand-in for BAND that delivers messages as JSON text, so a whole run needs no keys."""

import pytest

from plan_my_stay import demo
from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.messages import Message, build_message
from plan_my_stay.rehearsal.room import Room, RoomError, RoomLoopError
from plan_my_stay.seat import TextReply


def _msg(sender: Seat, to: list[Seat], summary: str = "ping") -> Message:
    return build_message(
        type=MessageType.ERROR, sender=sender, to=to, trip_id=demo.TRIP_ID, user_id=demo.USER_ID,
        state_version=0, summary=summary, payload={"code": "test", "bad_message_id": None, "fields": []},
    )


class Recorder:
    """A seat that records what it gets and answers with whatever it was told to."""

    def __init__(self, answers=()):
        self.received: list[Message] = []
        self._answers = list(answers)

    def handle(self, message: Message):
        self.received.append(message)
        return [self._answers.pop(0)] if self._answers else []


class TalkingConcierge(Recorder):
    def handle_text(self, sender: str, text: str):
        return [TextReply(text=f"hello {sender}: {text}", mentions=(sender,)), _msg(Seat.CONCIERGE, [Seat.GYM])]


def test_a_message_reaches_only_the_seats_named_in_to():
    gym, stay = Recorder(), Recorder()
    room = Room({Seat.CONCIERGE: TalkingConcierge(), Seat.GYM: gym, Seat.STAY: stay})

    room.say("Mario", "plan it")

    assert len(gym.received) == 1 and stay.received == []


def test_messages_cross_the_room_as_json_text_and_arrive_equal():
    sent = _msg(Seat.CONCIERGE, [Seat.GYM], "exactly this")
    gym = Recorder()
    concierge = TalkingConcierge()
    concierge.handle_text = lambda sender, text: [sent]
    Room({Seat.CONCIERGE: concierge, Seat.GYM: gym}).say("Mario", "go")

    assert gym.received == [sent]


def test_text_replies_are_collected_for_the_person():
    room = Room({Seat.CONCIERGE: TalkingConcierge(), Seat.GYM: Recorder()})
    room.say("Mario", "plan it")

    assert [r.text for r in room.replies] == ["hello Mario: plan it"]


def test_answers_are_delivered_in_order_until_the_room_is_quiet():
    first = _msg(Seat.GYM, [Seat.STAY], "from gym")
    stay = Recorder(answers=[_msg(Seat.STAY, [Seat.CONCIERGE], "from stay")])
    concierge = TalkingConcierge()
    room = Room({Seat.CONCIERGE: concierge, Seat.GYM: Recorder(answers=[first]), Seat.STAY: stay})

    room.say("Mario", "go")

    assert [e.summary for e in room.log] == ["ping", "from gym", "from stay"]
    assert [m.summary for m in concierge.received] == ["from stay"]


def test_two_seats_answering_each_other_forever_hit_the_step_cap():
    class Echo:
        def __init__(self, me: Seat, other: Seat):
            self.me, self.other = me, other

        def handle(self, message):
            return [_msg(self.me, [self.other])]

    concierge = TalkingConcierge()
    room = Room({Seat.CONCIERGE: concierge, Seat.GYM: Echo(Seat.GYM, Seat.STAY), Seat.STAY: Echo(Seat.STAY, Seat.GYM)}, max_steps=20)

    with pytest.raises(RoomLoopError):
        room.say("Mario", "go")


def test_sending_to_a_seat_that_is_not_in_the_room_is_an_error():
    concierge = TalkingConcierge()  # addresses the gym, which is not registered
    with pytest.raises(RoomError, match="gym"):
        Room({Seat.CONCIERGE: concierge}).say("Mario", "go")


def test_the_room_keeps_every_machine_message_for_inspection():
    first = _msg(Seat.GYM, [Seat.STAY], "from gym")
    stay = Recorder(answers=[_msg(Seat.STAY, [Seat.CONCIERGE], "from stay")])
    room = Room({Seat.CONCIERGE: TalkingConcierge(), Seat.GYM: Recorder(answers=[first]), Seat.STAY: stay})

    room.say("Mario", "go")

    assert [m.summary for m in room.messages] == ["ping", "from gym", "from stay"]
    assert all(isinstance(m, Message) for m in room.messages)


def test_the_transcript_is_readable():
    room = Room({Seat.CONCIERGE: TalkingConcierge(), Seat.GYM: Recorder()})
    room.say("Mario", "plan it")

    text = room.format_transcript()
    assert "concierge -> gym" in text and "ping" in text and "hello Mario" in text
