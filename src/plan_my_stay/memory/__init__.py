"""Shared memory (Cognee): `CogneeMemory` for real runs, `InMemoryMemory` for tests and offline runs.

Both implement `plan_my_stay.ports.MemoryPort` and pass the same contract suite (tests/memory/contract.py).
"""

from plan_my_stay.memory.cognee_client import CogneeClient, CogneeError
from plan_my_stay.memory.cognee_memory import CogneeMemory
from plan_my_stay.memory.in_memory import InMemoryMemory

__all__ = ["CogneeClient", "CogneeError", "CogneeMemory", "InMemoryMemory"]
