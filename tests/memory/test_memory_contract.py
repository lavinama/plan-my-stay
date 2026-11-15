"""Runs the MemoryPort contract against InMemoryMemory and against CogneeMemory (over a fake Cognee)."""

from .contract import MemoryContract


class TestMemoryContract(MemoryContract):
    """The `backend` fixture is parametrized, so each inherited test runs once per implementation."""
