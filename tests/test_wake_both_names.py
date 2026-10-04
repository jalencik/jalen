"""
Both names wake him — the acoustic half of "Jarvis should work everywhere".

The text router can accept any spelling of either name for free. The wake
word cannot: hey_jalen was trained with "Hey Jarvis" as an explicit
NEGATIVE, so it scores the old name at 0.00 by design and no threshold
recovers it. Only loading the pretrained hey_jarvis model alongside it makes
the old name wake him at all.

These tests are about the wiring — that both models load and both are
consulted. The acoustic accuracy itself is measured by
scripts/train_wake_word.py, against held-out audio.
"""
from __future__ import annotations

import pytest

from jalen.audio.wake import WakeWord
from jalen.config import CONFIG


class Cfg:
    def __init__(self, **over):
        self._d = {
            "wake.enabled": True,
            "wake.model": "hey_jalen",
            "wake.threshold": 0.7,
            "wake.cooldown_s": 1.5,
            **over,
        }

    def get_path(self, key, default=None):
        return self._d.get(key, default)


def test_a_single_model_name_still_works():
    """
    wake.model was a plain string for the whole life of the project. Every
    older config and every existing test passes one, and they must not break.
    """
    wake = WakeWord(Cfg(**{"wake.model": "hey_jalen"}))
    assert wake.model_names == ["hey_jalen"]
    assert wake.model_name == "hey_jalen"


def test_a_list_of_models_is_accepted():
    wake = WakeWord(Cfg(**{"wake.model": ["hey_jalen", "hey_jarvis_v0.1"]}))
    assert wake.model_names == ["hey_jalen", "hey_jarvis_v0.1"]


def test_the_shipped_config_loads_both_names():
    """
    The behaviour is only real if the config asks for it. A passing test over
    a single-model config would prove nothing about what he actually runs.
    """
    wake = WakeWord(CONFIG)
    assert len(wake.model_names) >= 2, (
        f"only {wake.model_names} is configured — the old name will not wake him"
    )
    joined = " ".join(wake.model_names).lower()
    assert "jalen" in joined
    assert "jarvis" in joined


def test_both_models_are_really_loaded_into_the_runtime():
    """
    Not just configured — present in openWakeWord's own model map, which is
    what predict() iterates. A name that fails to resolve is dropped
    silently, and the symptom is a wake word that simply never fires.
    """
    wake = WakeWord(CONFIG)
    wake.load()
    loaded = " ".join(wake._model.models.keys()).lower()
    assert "jalen" in loaded, f"hey_jalen did not load (got {list(wake._model.models)})"
    assert "jarvis" in loaded, f"hey_jarvis did not load (got {list(wake._model.models)})"


def test_the_score_is_the_best_of_all_models():
    """
    feed() takes the max across models. Averaging, or reading only the first,
    would mean the second name never crosses the threshold no matter how
    clearly it was said.
    """
    import inspect

    source = inspect.getsource(WakeWord.feed)
    assert "max(" in source, "feed() no longer takes the best score across models"


def test_silence_wakes_neither_model():
    """Two models is two chances to false-fire; neither may fire on nothing."""
    import numpy as np

    wake = WakeWord(CONFIG)
    wake.load()
    for _ in range(60):
        assert not wake.feed(np.zeros(512, dtype=np.float32))
    assert wake.last_score < 0.1
