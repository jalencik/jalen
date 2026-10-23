"""
"Constant memory."                                      - his words, 2026-10-23

Two gaps in what Jalen remembers about him (jalen/tools/memory.py):

1. PRIVATE MODE DID NOT REACH MEMORY. "Private mode on. Nothing is being
   logged or remembered." - the audit log stopped writing, and remember()
   went on storing, now including what the brain stores on its own when he
   says something lasting (agent.py, MEMORY). memory.set_private() is set from
   the audit log's own switch, at start and whenever private mode changes.

2. ONLY THE BRAIN COULD STORE A FACT, when it chose to - report.md found the
   memory table had never held a row. "Remember that ..." and "what do you
   remember about ..." are now free spoken commands, in his own words. Not
   "remember to ..." (a reminder), not "remember this" (what it means is in
   the conversation), and not "what do you know about quantum computing",
   which is a question for the brain.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jalen.config import CONFIG
from jalen.tools import memory


def _store(tmp_path):
    cfg = SimpleNamespace(get_path=lambda key, default=None:
                          str(tmp_path / "memory.db") if key == "memory.db_path" else default)
    return memory.MemoryStore(cfg)


# --------------------------------------------------------------------------
# 1. PRIVATE MODE MEANS NOTHING IS REMEMBERED
# --------------------------------------------------------------------------
def test_in_private_mode_nothing_is_stored_and_it_says_so(tmp_path):
    store = _store(tmp_path)
    memory.set_private(True)
    reply = store.remember("my sister is called Dilnoza")
    assert reply.startswith("Private mode is on, so I'm not storing anything")
    assert store._conn is None, "it opened the memory database in private mode"
    assert not (tmp_path / "memory.db").exists()


def test_a_credential_is_still_refused_outside_private_mode(tmp_path):
    memory.set_private(False)
    reply = _store(tmp_path).remember("my wifi password is: hunter2hunter2")
    assert "credential" in reply


def _jalen_with(private: bool):
    from jalen.app import Jalen

    j = Jalen.__new__(Jalen)
    j.audit = SimpleNamespace(private=private,
                              set_private_mode=lambda on: setattr(j.audit, "private", on))
    return j


@pytest.mark.parametrize("on", [True, False])
def test_the_private_mode_command_reaches_memory(on):
    from jalen.brain.router import Intent

    memory.set_private(not on)
    j = _jalen_with(private=not on)
    j.handle_local(Intent("private_mode", {"on": on}, "Private mode."))
    assert memory._PRIVATE.is_set() is on


def test_a_jalen_that_starts_in_private_mode_starts_memory_private():
    """memory.private_mode_default starts the audit log private; memory follows."""
    import inspect

    from jalen.app import Jalen

    source = inspect.getsource(Jalen.__init__)
    assert "set_private(self.audit.private)" in source
    assert source.index("AuditLog(cfg") < source.index("set_private(self.audit.private)")


# --------------------------------------------------------------------------
# 2. SAID ALOUD, IN HIS OWN WORDS
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def router():
    from jalen.brain.router import IntentRouter

    return IntentRouter(CONFIG)


@pytest.mark.parametrize("said, stored", [
    ("remember that my sister is called Dilnoza", "my sister is called Dilnoza"),
    ("Jalen, remember that I take my coffee black.", "I take my coffee black"),
    ("please remember my exam is on June 5", "my exam is on June 5"),
])
def test_remember_that_stores_his_words(router, said, stored):
    hit = router.route(said)
    assert (hit.tool, hit.args) == ("remember", {"text": stored})


@pytest.mark.parametrize("said", ["remember to buy milk", "remember this", "remember that",
                                  "remember it", "remember when we went to Tashkent",
                                  "remember me", "remember"])
def test_what_needs_the_conversation_is_left_to_the_brain(router, said):
    hit = router.route(said)
    assert hit is None or hit.tool != "remember", said


@pytest.mark.parametrize("said, query", [
    ("do you remember my sister's name?", "my sister's name"),
    ("what do you remember about Dilnoza", "Dilnoza"),
])
def test_what_do_you_remember_searches_his_memory(router, said, query):
    hit = router.route(said)
    assert (hit.tool, hit.args) == ("recall_memory", {"query": query})


@pytest.mark.parametrize("said", ["what do you know about me", "what do you remember about me"])
def test_about_me_searches_for_his_own_facts_not_the_word_me(router, said):
    hit = router.route(said)
    assert hit.tool == "recall_memory" and hit.args["query"].startswith("about me:")


@pytest.mark.parametrize("said", ["what do you know about quantum computing",
                                  "do you remember when we talked about the lab"])
def test_a_question_for_the_brain_is_not_a_memory_search(router, said):
    hit = router.route(said)
    assert hit is None or hit.tool != "recall_memory", said


def test_storing_a_fact_said_aloud_is_allowed_after_a_read_but_not_from_it():
    """remember is on refuse_from_content: his words store, a page's do not."""
    from jalen.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    assert engine.classify("remember", {"text": "x"}, origin="user").tier is Tier.GREEN
    assert engine.classify("remember", {"text": "x"}, origin="content").tier is Tier.BLACK
