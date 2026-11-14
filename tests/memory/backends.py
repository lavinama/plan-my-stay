"""The two stores the contract suite runs against. Every `new_memory` shares one store, as every process
shares one Cognee dataset, so a test can restart "the process" by asking for a fresh memory over the same data."""

import datetime as dt
from collections.abc import Callable
from typing import Protocol

from plan_my_stay.memory.cognee_client import CogneeClient
from plan_my_stay.memory.cognee_memory import CogneeMemory
from plan_my_stay.memory.in_memory import InMemoryMemory, MemoryStore
from plan_my_stay.ports import MemoryPort

from .fake_cognee import FakeCognee
from .helpers import make_client


class Backend(Protocol):
    def new_memory(self, clock: Callable[[], dt.datetime]) -> MemoryPort:
        """A fresh memory over the same stored data, as if the process had restarted."""
        ...

    def session_entries(self, session_id: str) -> list[str]:
        """The raw text of every entry written to this trip session (QA text and traces)."""
        ...

    def permanent_entries(self) -> list[str]:
        """The raw text of every entry written straight to permanent (shared) memory."""
        ...

    def close(self) -> None: ...


class InMemoryBackend:
    def __init__(self) -> None:
        self.store = MemoryStore()

    def new_memory(self, clock: Callable[[], dt.datetime]) -> MemoryPort:
        return InMemoryMemory(store=self.store, clock=clock)

    def session_entries(self, session_id: str) -> list[str]:
        return [*self.store.sessions.get(session_id, ()), *self.store.traces.get(session_id, ())]

    def permanent_entries(self) -> list[str]:
        return list(self.store.permanent)

    def close(self) -> None:
        pass


class CogneeBackend:
    def __init__(self, fake: FakeCognee) -> None:
        self.fake = fake
        self._clients: list[CogneeClient] = []

    def new_memory(self, clock: Callable[[], dt.datetime]) -> MemoryPort:
        client = make_client()
        self._clients.append(client)
        return CogneeMemory(client, clock=clock)

    def session_entries(self, session_id: str) -> list[str]:
        session = self.fake.sessions.get(session_id, {"qas": [], "traces": []})
        return [*(qa["answer"] for qa in session["qas"]), *(t["method_return_value"] for t in session["traces"])]

    def permanent_entries(self) -> list[str]:
        return list(self.fake.permanent)

    def close(self) -> None:
        for client in self._clients:
            client.close()
