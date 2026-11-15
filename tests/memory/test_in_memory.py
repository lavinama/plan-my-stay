"""InMemoryMemory specifics: it is a drop-in for MemoryPort, isolated per instance, and inspectable by seat tests."""

from plan_my_stay.enums import Seat
from plan_my_stay.memory.in_memory import InMemoryMemory
from plan_my_stay.models import Profile
from plan_my_stay.ports import MemoryPort

from .helpers import FakeClock


def test_it_satisfies_the_memory_port() -> None:
    memory: MemoryPort = InMemoryMemory()

    assert memory.load_profile("u") == Profile()


def test_instances_do_not_share_data(clock: FakeClock) -> None:
    first, second = InMemoryMemory(clock=clock), InMemoryMemory(clock=clock)

    first.save_preference(user_id="u", trip_id="t", key="diet", value=["vegan"], evidence="e")

    assert first.load_profile("u").diet == ("vegan",)
    assert second.load_profile("u") == Profile()


def test_traces_are_inspectable_per_trip_and_start_with_the_agent(clock: FakeClock) -> None:
    memory = InMemoryMemory(clock=clock)

    memory.log_trace(trip_id="t1", user_id="u", agent=Seat.GYM, step="search", outcome="ok")
    memory.log_trace(trip_id="t2", user_id="u", agent=Seat.STAY, step="read", outcome="bad")

    (line,) = memory.trace_lines("t1")
    assert line.startswith("agent=gym ") and "step=search" in line and line.endswith("outcome=ok")
    assert memory.trace_lines("nope") == ()


def test_consolidation_calls_are_recorded_for_seat_tests(clock: FakeClock) -> None:
    memory = InMemoryMemory(clock=clock)

    memory.consolidate("u1")
    memory.consolidate("u2")

    assert memory.consolidated_users == ("u1", "u2")


def test_it_works_with_the_real_clock_by_default() -> None:
    memory = InMemoryMemory()
    memory.save_preference(user_id="u", trip_id="t", key="diet", value=["a"], evidence="e")
    memory.save_preference(user_id="u", trip_id="t", key="diet", value=["b"], evidence="e")

    assert memory.load_profile("u").diet == ("b",)
