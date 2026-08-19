"""
Tests for jarvis/tools/memory.py (Phase E). Real fastembed model, real
sqlite-vec storage — no mocks standing in for the embedding/retrieval
mechanism itself, since that mechanism is exactly what's under test.
Uses a real, isolated on-disk database per test (not :memory:, since
persistence-across-restart is one of the things being verified) rather
than the real data/jarvis.db.

Slower than the rest of the suite by nature: the embedding model loads
once (cached after the first run anywhere on this machine) and each
remember()/recall() runs a real embedding.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.config import CONFIG, Cfg  # noqa: E402
from jarvis.tools.memory import MemoryStore, looks_like_a_secret  # noqa: E402


@pytest.fixture
def store(tmp_path):
    cfg = Cfg(dict(CONFIG))
    cfg["memory"] = {**CONFIG.get("memory", {}), "db_path": str(tmp_path / "test_memory.db")}
    s = MemoryStore(cfg)
    yield s
    s.close()


# ------------------------------------------------------------ secret guard
@pytest.mark.parametrize(
    "text",
    [
        "remember my wifi password is: hunter2superlongpassword123456",
        "my api_key: sk-abc123456789012345",
        "the password for my router is Sunshine2024!!",
        "here's my groq token gsk_abcdefghijklmnopqrstuvwx",
    ],
)
def test_secret_shaped_text_is_detected(text):
    assert looks_like_a_secret(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "I like my coffee black, no sugar.",
        "My favorite programming language is Python.",
        "The weather today is sunny.",
        "my birthday is January 5th",
        "call me boss",
    ],
)
def test_ordinary_memories_are_not_flagged(text):
    assert looks_like_a_secret(text) is False


def test_remember_refuses_secret_shaped_text(store):
    result = store.remember("my api_key: sk-abc123456789012345")
    assert "credential" in result.lower()


# -------------------------------------------------------------- remember/recall
def test_remember_empty_text_is_a_no_op(store):
    result = store.remember("   ")
    assert "nothing" in result.lower()


def test_recall_before_anything_stored(store):
    result = store.recall("anything")
    assert "nothing stored" in result.lower() or "don't have anything" in result.lower()


def test_remember_and_recall_relevance(store):
    """The actual point of this module: retrieval must be relevant, not
    just present. Real semantic search, not a keyword match."""
    store.remember("I like my coffee black, no sugar.")
    store.remember("My favorite programming language is Python.")
    store.remember("The weather today is sunny.")

    result = store.recall("what do I put in my coffee")

    lines = result.strip().split("\n")
    assert "coffee" in lines[0].lower(), f"most relevant result should be the coffee memory, got: {lines[0]!r}"


def test_persistence_across_restart(tmp_path):
    """A fresh MemoryStore instance pointed at the same db file must see
    everything a previous instance stored — this is what 'persistence
    across restart' actually means, not just 'the file exists'."""
    cfg = Cfg(dict(CONFIG))
    db_path = str(tmp_path / "persist_test.db")
    cfg["memory"] = {**CONFIG.get("memory", {}), "db_path": db_path}

    first = MemoryStore(cfg)
    first.remember("I like my coffee black, no sugar.")
    first.close()

    second = MemoryStore(cfg)
    result = second.recall("coffee preferences")
    second.close()

    assert "coffee" in result.lower()


def test_disabled_memory_refuses_politely(tmp_path):
    cfg = Cfg(dict(CONFIG))
    cfg["memory"] = {**CONFIG.get("memory", {}), "enabled": False, "db_path": str(tmp_path / "off.db")}
    s = MemoryStore(cfg)
    assert "off" in s.remember("anything").lower()
    assert "off" in s.recall("anything").lower()


# -------------------------------------------------------------------- dispatch
def test_registered_in_unified_tool_registry():
    from jarvis import tools

    assert "remember" in tools.REGISTRY
    assert "recall_memory" in tools.REGISTRY


def test_memory_tools_are_green_tier():
    from jarvis.safety import SafetyEngine

    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    assert engine.classify("remember", {}).tier.value == "green"
    assert engine.classify("recall_memory", {}).tier.value == "green"
