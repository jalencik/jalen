"""
"Make sure that hermes has been working."              - his words, 2026-10-23

Hermes (Nous Research's Hermes 4, reached through OpenRouter) has had a
working route in jalen/tools/agents.py since September, and three things kept
it out of reach:

  * the brain's delegate_task spec said the agent is "gemini or chatgpt", so
    the brain was never told Hermes was there. The real session in which he
    asked about Hermes ended with Jalen asking "which Hermes do you mean?"
    (tests/test_env_documentation.py tells that story);
  * the agent's name was looked up exactly, so "Hermes 4" or "Nous Research"
    found no route - and the reply listing the routes left Hermes out;
  * `jalen check`, the diagnostic he is told to run first, said nothing about
    whether a Hermes (or a ChatGPT) key was there at all.

NOT CHECKED FROM HERE: a live call. There is no key on this machine. The last
word from the real endpoint is the 402 of 20 September, which proved the key
and the model name were accepted (tests/test_delegate_token_bound.py).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jalen.tools import agents

ROOT = Path(__file__).resolve().parent.parent

BRIEF = (
    "Context: he is choosing a speech-to-text setup for a Windows laptop with "
    "8 GB of RAM and about 1 GB free, so nothing heavy can run locally right "
    "now. Objective: compare three hosted options on latency, price and "
    "accuracy for English, Uzbek and Russian. Success looks like a table with "
    "one row per option and a one-line recommendation. Do not suggest anything "
    "that needs PyTorch. If something is unclear, say what you assumed."
)


class _OpenAI:
    """Shaped like openai.OpenAI far enough to record the one call made."""

    made: list = []

    def __init__(self, api_key=None, base_url=None, **_kw):
        self.api_key = api_key
        self.base_url = base_url

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        # A copy: the caller appends the answer to the same list afterwards.
        _OpenAI.made.append((self.base_url, dict(kwargs, messages=list(kwargs["messages"]))))
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="Option A is fastest; take it."))])


@pytest.fixture
def hermes(monkeypatch, tmp_path):
    import openai

    _OpenAI.made = []
    monkeypatch.setattr(openai, "OpenAI", _OpenAI)
    monkeypatch.setattr(agents, "CHATS_PATH", tmp_path / "agent_chats.json")
    monkeypatch.delenv("HERMES_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    return _OpenAI


# --------------------------------------------------------------------------
# 1. THE BRAIN IS TOLD
# --------------------------------------------------------------------------
def test_the_brain_is_told_hermes_is_there():
    from jalen.brain.tools import TOOL_SPECS

    description, params = TOOL_SPECS["delegate_task"]
    assert "hermes" in description.lower() and "ask Hermes" in description
    assert "hermes" in params["agent"][1]


# --------------------------------------------------------------------------
# 2. THE NAME HE USES FINDS IT
# --------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["hermes", "Hermes", "Hermes 4", "hermes-4-405b",
                                  "Hermes 4 405B", "Nous Research", "nous"])
def test_every_way_of_naming_hermes_reaches_hermes(said):
    name, ask = agents._agent_named(said)
    assert (name, ask) == ("hermes", agents.AGENTS["hermes"]), said


@pytest.mark.parametrize("said, route", [
    ("chat gpt", "chatgpt"), ("ChatGPT", "chatgpt"), ("GPT-4o", "chatgpt"),
    ("openai", "chatgpt"), ("Gemini 2.5 Pro", "gemini"), ("gemini", "gemini"),
])
def test_the_other_routes_are_found_the_same_way(said, route):
    assert agents._agent_named(said)[0] == route


def test_a_name_with_no_route_is_told_hermes_is_one(hermes):
    reply = agents.delegate_task("grok", BRIEF)
    assert reply.startswith("I don't have a route to 'grok'")
    assert "hermes (needs an OpenRouter key)" in reply
    assert hermes.made == []


# --------------------------------------------------------------------------
# 3. ASKING HERMES REACHES HERMES
# --------------------------------------------------------------------------
def test_asking_hermes_4_goes_to_openrouter_with_the_hermes_model(hermes):
    reply = agents.delegate_task("Hermes 4", BRIEF, expectation="which one to use")
    assert reply.startswith("hermes answered")
    assert "Option A is fastest" in reply
    (base_url, call), = hermes.made
    assert base_url == agents.HERMES_BASE_URL
    assert call["model"] == agents.HERMES_MODEL
    assert 0 < call["max_tokens"] <= 4000, "unbounded: the September 402"


def test_a_follow_up_goes_back_to_hermes_with_the_whole_conversation(hermes):
    agents.delegate_task("Nous Research", BRIEF)
    reply = agents.follow_up_task("", "Add the price per hour of audio.")
    assert reply.startswith("Round 2 with hermes")
    roles = [m["role"] for m in hermes.made[-1][1]["messages"]]
    assert roles == ["user", "assistant", "user"]


def test_no_key_is_said_plainly_and_names_both_keys(hermes, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    reply = agents.delegate_task("hermes", BRIEF)
    assert reply.startswith("Couldn't reach hermes: There's no HERMES_API_KEY or "
                            "OPENROUTER_API_KEY")
    assert hermes.made == []


# --------------------------------------------------------------------------
# 4. `jalen check` SAYS WHETHER IT CAN WORK
# --------------------------------------------------------------------------
@pytest.fixture
def check_env():
    spec = importlib.util.spec_from_file_location("check_env_hermes_probe",
                                                  ROOT / "scripts" / "check_env.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_env_hermes_probe"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("check_env_hermes_probe", None)


def _row(check_env, name):
    from jalen.config import SECRETS

    return next(r for r in check_env._credential_checks(SECRETS) if r[0] == name)


@pytest.mark.parametrize("variable", ["HERMES_API_KEY", "OPENROUTER_API_KEY"])
def test_jalen_check_sees_either_hermes_key(check_env, monkeypatch, variable):
    monkeypatch.delenv("HERMES_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert not _row(check_env, "HERMES_API_KEY or OPENROUTER_API_KEY")[1]
    monkeypatch.setenv(variable, "sk-or-v1-test")
    name, value, required, why = _row(check_env, "HERMES_API_KEY or OPENROUTER_API_KEY")
    assert value and required is False and why == "asking Hermes"


def test_jalen_check_sees_the_chatgpt_key_too(check_env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert _row(check_env, "OPENAI_API_KEY")[3] == "asking ChatGPT"
