"""Test isolation: clear the gym worker's in-memory state before every test.

Without this, state left over from one test (e.g. a finalized Tuesday item)
could leak into the next test's assertions, since workers/gym/state.py is a
module-level dict shared by the whole process.
"""

import pytest

from workers.gym import state


@pytest.fixture(autouse=True)
def _reset_gym_state():
    state.reset()
    yield
    state.reset()
