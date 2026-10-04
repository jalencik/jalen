"""
Long writing, and a lot of it at once.

Two requests nothing else covered: "write essays for me using my voice
skill" (which needed somewhere for the essay to GO), and "writing 50+ posts,
and being able to send those posts to my saved messages... it should be able
to handle that kinda combo".
"""
from __future__ import annotations

import pytest

from jalen.tools import drafting


@pytest.fixture(autouse=True)
def temp_drafts(tmp_path, monkeypatch):
    monkeypatch.setattr(drafting, "DRAFTS_DIR", tmp_path / "drafts")
    yield tmp_path / "drafts"


# ------------------------------------------------------------------ essays
def test_a_draft_is_written_and_readable_back(temp_drafts):
    reply = drafting.save_draft_text("MIT personal statement", "Hello. " * 100)
    assert "100 words" in reply
    files = list(temp_drafts.glob("*.md"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8").startswith("Hello.")


def test_a_second_attempt_does_not_replace_the_first(temp_drafts):
    """
    He iterates on these. A rewrite silently overwriting the previous draft
    is how a better first attempt disappears.
    """
    import time

    drafting.save_draft_text("essay", "first attempt")
    time.sleep(1.05)          # the stamp is per-second
    drafting.save_draft_text("essay", "second attempt")
    bodies = {p.read_text(encoding="utf-8") for p in temp_drafts.glob("*.md")}
    assert bodies == {"first attempt", "second attempt"}


def test_an_empty_draft_is_refused(temp_drafts):
    assert "nothing to save" in drafting.save_draft_text("essay", "   ").lower()
    assert not temp_drafts.exists() or not list(temp_drafts.glob("*.md"))


@pytest.mark.parametrize(
    "name, expected",
    [
        ("MIT personal statement", "mit-personal-statement"),
        ("Why this programme?!", "why-this-programme"),
        ("", "draft"),
        ("///", "draft"),
    ],
)
def test_names_become_safe_filenames(name, expected):
    assert drafting._slug(name) == expected


def test_no_drafts_yet_says_so(temp_drafts):
    assert "No drafts yet" in drafting.list_drafts()


# ------------------------------------------------------------- many posts
def _fake_sender(results):
    calls = []

    def send(to, text):
        calls.append((to, text))
        return results.pop(0)

    return send, calls


def test_several_posts_go_out_in_one_call(monkeypatch):
    """
    One tool call per post would exhaust a 30-turn budget around the eighth.
    """
    send, calls = _fake_sender(["Sent to X"] * 3)
    monkeypatch.setattr("jalen.tools.messaging.send_telegram_message", send)

    reply = drafting.send_posts("Saved Messages", ["one", "two", "three"])
    assert len(calls) == 3
    assert "3 of 3 sent" in reply


def test_failures_are_listed_never_rounded_away(monkeypatch):
    """
    THE ONE THAT MATTERS. "Sent 50 posts" when eleven failed is the
    silent-omission failure in its purest form — he finds out days later,
    from the people who never got them.
    """
    send, _calls = _fake_sender(
        ["Sent to X", "I couldn't find a Telegram chat called 'X'", "Sent to X"]
    )
    monkeypatch.setattr("jalen.tools.messaging.send_telegram_message", send)

    reply = drafting.send_posts("X", ["a", "b", "c"])
    assert "2 of 3 sent" in reply
    assert "FAILED (1)" in reply
    assert "couldn't find" in reply


def test_an_exception_on_one_post_does_not_lose_the_rest(monkeypatch):
    def send(to, text):
        if text == "b":
            raise RuntimeError("network died")
        return "Sent to X"

    monkeypatch.setattr("jalen.tools.messaging.send_telegram_message", send)
    reply = drafting.send_posts("X", ["a", "b", "c"])
    assert "2 of 3 sent" in reply
    assert "RuntimeError" in reply


def test_an_oversized_batch_is_refused():
    """A mistake repeated fifty times is worse than one mistake."""
    reply = drafting.send_posts("X", ["post"] * 200)
    assert "more than I'll do in one go" in reply


def test_batching_drafts_is_refused_because_telegram_keeps_one(monkeypatch):
    """
    Telegram stores ONE draft per chat. Saving fifty would leave the last,
    and reporting fifty saved would be false in a way he only finds by
    opening the chat.
    """
    reply = drafting.send_posts("X", ["a", "b"], as_draft=True)
    assert "one draft per chat" in reply
    assert "only the last one" in reply


def test_a_single_draft_is_fine(monkeypatch):
    saved = []
    monkeypatch.setattr(
        "jalen.tools.messaging.save_telegram_draft",
        lambda to, text: saved.append(text) or "Saved as a draft in X",
    )
    reply = drafting.send_posts("X", ["only one"], as_draft=True)
    assert saved == ["only one"]
    assert "1 of 1" in reply


def test_empty_and_blank_posts_are_dropped(monkeypatch):
    send, calls = _fake_sender(["Sent to X"])
    monkeypatch.setattr("jalen.tools.messaging.send_telegram_message", send)
    drafting.send_posts("X", ["real post", "", "   "])
    assert len(calls) == 1


def test_a_non_list_is_rejected_clearly():
    assert "list of strings" in drafting.send_posts("X", "not a list")


# ----------------------------------------------------------- reachability
def test_tiers():
    """
    Writing a file locally is GREEN. Sending posts reaches people, so it
    keeps the same tier a single message has — batching the WORK, not the
    permission.
    """
    from jalen import tools
    from jalen.brain.tools import TOOL_SPECS
    from jalen.config import CONFIG
    from jalen.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    for name, tier in (
        ("save_draft_text", Tier.GREEN),
        ("list_drafts", Tier.GREEN),
        ("send_posts", Tier.RED),
    ):
        assert name in tools.REGISTRY and name in TOOL_SPECS
        assert engine.classify(name, {}).tier is tier
