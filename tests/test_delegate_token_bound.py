"""
A delegated question must bound its own answer length.

Found by running it, 20 September 2026, the first real Hermes call ever made
from this repo:

    402 - "This request requires more credits, or fewer max_tokens.
           You requested up to 117952 tokens, but can only afford 13333."

The key was correct -- a bad key is 401, and 402 means OpenRouter accepted it,
identified the account and checked the balance. The failure was ours:
_ask_hermes and _ask_chatgpt both call chat.completions.create() with model and
messages only, so the endpoint defaults max_tokens to the model's whole context
window and the provider pre-authorises against all of it. On a pay-as-you-go
balance that is a hard refusal before a single token is generated.

It is nonsense for this application even when credit allows it. Jalen SPEAKS
these answers. The repo's own figure is SPOKEN_CHARS_PER_SECOND = 22.4, so
117,952 tokens is roughly 470,000 characters, about six hours of continuous
speech, requested for a question like "which Hermes did you mean". The default
below is 4,000 tokens: about 16,000 characters, ~12 minutes spoken -- already
far past anything summarise_if_long would let through, and generous for the
written drafts delegate_task is also used for, while sitting comfortably inside
the 13,333 this account could afford.

The knob lives in config/jalen.yaml AND is read here, and the fourth test below
pins both halves. agents.py:74-79 is the post-mortem of getting that wrong the
other way round: jalen.yaml carried brain.gemini_model, no code read it, and
editing the documented setting did nothing -- "worse than having no setting",
because the file's own header promises no code changes are needed.

NOT COVERED, deliberately: _ask_gemini is unbounded in the same way
(generate_content with no max_output_tokens) but it is WORKING today and the
defect has never bitten there, so bounding it would be changing live code on
inference rather than on a reproduction. Left as a separate, reported item.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jalen.tools import agents  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class _RecordingClient:
    """Shaped like openai.OpenAI just far enough to capture the call."""

    last_kwargs: dict = {}

    def __init__(self, api_key=None, base_url=None, **_kw):
        self.api_key = api_key
        self.base_url = base_url
        _RecordingClient.last_kwargs = {}

    # client.chat.completions.create(...)
    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        _RecordingClient.last_kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )


@pytest.fixture
def recording_openai(monkeypatch):
    """Substitute openai.OpenAI. agents.py imports it inside the call, so
    patching the module attribute is what the call actually resolves."""
    import openai

    monkeypatch.setattr(openai, "OpenAI", _RecordingClient)
    _RecordingClient.last_kwargs = {}
    return _RecordingClient


def test_hermes_bounds_its_answer_so_a_funded_account_is_not_refused(
    monkeypatch, recording_openai
):
    """The reproduced failure. Without max_tokens the provider pre-authorises
    the model's entire context window and refuses with 402 before generating
    anything."""
    monkeypatch.setenv("HERMES_API_KEY", "sk-or-v1-test")

    answer, error = agents._ask_hermes([{"role": "user", "content": "hi"}])

    assert error is None, error
    sent = recording_openai.last_kwargs
    assert "max_tokens" in sent, (
        "no max_tokens: the endpoint defaults to the whole context window, "
        "which is what produced the live 402"
    )
    assert isinstance(sent["max_tokens"], int) and sent["max_tokens"] > 0


def test_chatgpt_bounds_its_answer_too(monkeypatch, recording_openai):
    """Same code shape, same defect, same provider behaviour on a low balance.
    Fixing only the one that happened to fail would leave the other waiting."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    answer, error = agents._ask_chatgpt([{"role": "user", "content": "hi"}])

    assert error is None, error
    assert "max_tokens" in recording_openai.last_kwargs


def test_the_bound_is_read_from_config_not_frozen_in_code(
    monkeypatch, recording_openai
):
    """Per this repo's rule that everything changeable lives in jalen.yaml.
    Setting the documented key must actually change the request."""
    monkeypatch.setenv("HERMES_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr(agents, "_delegate_max_tokens", lambda: 777)

    agents._ask_hermes([{"role": "user", "content": "hi"}])

    assert recording_openai.last_kwargs["max_tokens"] == 777


def test_the_knob_is_both_documented_and_read(monkeypatch, recording_openai):
    """Both halves, because this repo has a post-mortem of each failure mode:
    a key in jalen.yaml that no code reads (agents.py:74-79), and a constant in
    code that jalen.yaml never mentions. Neither is acceptable."""
    yaml_text = (ROOT / "config" / "jalen.yaml").read_text(encoding="utf-8")

    assert "delegate_max_tokens" in yaml_text, (
        "the knob is not in jalen.yaml, whose header promises every changeable "
        "value lives there"
    )

    monkeypatch.setenv("HERMES_API_KEY", "sk-or-v1-test")
    agents._ask_hermes([{"role": "user", "content": "hi"}])
    assert recording_openai.last_kwargs.get("max_tokens"), (
        "documented but unread is the exact trap agents.py:74-79 records"
    )


def test_an_unreadable_config_still_yields_a_bound(monkeypatch):
    """Config problems must never leave the request unbounded -- that is the
    failure being fixed. Same rule _gemini_model() follows with its own
    try/except: degrade to a working default, never to None."""
    import jalen.config

    def explode(*_a, **_k):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(jalen.config.CONFIG, "get_path", explode)

    assert agents._delegate_max_tokens() == 4000


@pytest.mark.parametrize("bad", [0, -1, None, ""])
def test_a_nonsense_configured_value_falls_back_rather_than_unbounding(
    monkeypatch, bad
):
    """A zero or empty value in jalen.yaml must not become "no limit". max_tokens=0
    is rejected by the API and None reinstates the whole-context-window default,
    so both have to floor to the working bound."""
    import jalen.config

    monkeypatch.setattr(
        jalen.config.CONFIG, "get_path", lambda key, default=None: bad
    )

    assert agents._delegate_max_tokens() == 4000
