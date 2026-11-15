"""Live specialist agents. One subpackage per seat: `stay`, `restaurant`, `gym` (Rohan, Jwalin, Prathik).

Convention: `plan_my_stay/agents/<seat>/__init__.py` exposes

    def build(settings: Settings) -> Seat: ...

returning an object with `handle(message) -> Sequence[Outgoing]` (see `plan_my_stay.seat`). Until that
module exists, `plan_my_stay.seats.build_seat` runs the rehearsal stub for the seat and logs a warning,
so every seat can be started on day one. Drop your agent into the rehearsal with
`build_rehearsal_seats(overrides={Seat.GYM: my_seat})`.
"""
