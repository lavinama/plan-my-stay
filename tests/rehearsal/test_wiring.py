"""The rehearsal cast: how a teammate swaps their live agent in for a stub and re-runs the demo."""

from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.rehearsal.room import Room
from plan_my_stay.rehearsal.stubs import GymStub, RestaurantStub, StayStub
from plan_my_stay.rehearsal.wiring import build_rehearsal_seats


class RecordingSeat:
    """A teammate's agent: records what it is asked and says nothing."""

    def __init__(self):
        self.received = []

    def handle(self, message):
        self.received.append(message)
        return []


def test_the_default_cast_is_all_six_seats_with_stand_ins_for_the_specialists():
    seats = build_rehearsal_seats()

    assert set(seats) == set(Seat)
    assert isinstance(seats[Seat.STAY], StayStub)
    assert isinstance(seats[Seat.RESTAURANT], RestaurantStub)
    assert isinstance(seats[Seat.GYM], GymStub)


def test_overrides_swap_exactly_the_named_seats():
    mine = RecordingSeat()

    seats = build_rehearsal_seats(overrides={Seat.GYM: mine})

    assert seats[Seat.GYM] is mine
    assert isinstance(seats[Seat.STAY], StayStub) and isinstance(seats[Seat.RESTAURANT], RestaurantStub)


def test_an_overridden_seat_is_the_one_the_concierge_talks_to():
    mine = RecordingSeat()
    room = Room(build_rehearsal_seats(overrides={Seat.GYM: mine}))

    room.say("Mario", "Plan my demo trip")

    assert [m.type for m in mine.received][:1] == [MessageType.REQUEST]
