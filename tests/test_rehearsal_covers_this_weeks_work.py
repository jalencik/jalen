"""
Nothing built on 23-24 October has been seen working on his machine - only
against fakes on a Linux box. `.\\jalen.ps1 rehearse` is how he drives a flow
end to end and records what he SAW, so the new flows are steps in it, and
`.\\jalen.ps1 todo` counts them from rehearse's own list instead of a "6" that
would have gone stale the day a step was added.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_probe", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_new_flows_are_rehearsed():
    keys = {step.key for step in _load("rehearse").STEPS}
    for key in ("keys_are_instant", "message_words_heard", "telegram_edit_delete",
                "scheduled_post", "conversation_after_restart", "channel_post_format"):
        assert key in keys, key


def test_every_step_asks_what_he_saw():
    for step in _load("rehearse").STEPS:
        assert "?" in step.observe, step.key
        assert step.say.strip(), step.key


def test_the_todo_count_is_rehearses_own():
    assert _load("whats_left")._rehearsal_flows() == len(_load("rehearse").STEPS) == 12
