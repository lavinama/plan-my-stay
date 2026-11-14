"""`plan_my_stay.seat.Seat` (the handler protocol) shares a name with `enums.Seat` (the seat names).
Three lanes tripped on that, so the protocol also has an unambiguous name."""

from plan_my_stay import enums, seat


def test_the_handler_protocol_has_an_unambiguous_name():
    assert seat.SeatHandler is seat.Seat
    assert seat.TextSeatHandler is seat.TextSeat


def test_the_unambiguous_names_are_not_the_enum():
    assert seat.SeatHandler is not enums.Seat
