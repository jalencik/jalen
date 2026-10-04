"""
The speech-cache cleaner lists only the clips the old test wrote, and deletes only on a typed yes.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "find_test_clips_under_test", ROOT / "scripts" / "find_test_clips_in_speech_cache.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cache(tmp_path, monkeypatch):
    from jarvis.audio.tts import Speaker
    from jarvis.config import CONFIG

    monkeypatch.setattr(Speaker, "CACHE_DIR", tmp_path / "tts_cache")
    speaker = Speaker(CONFIG)
    speaker.CACHE_DIR.mkdir(parents=True)
    return speaker


def _write(speaker, sentence, audio, rate):
    np.savez_compressed(speaker._cache_path(speaker._cache_key(sentence)), audio=audio, rate=rate)


def test_only_the_test_stubs_are_listed(cache):
    module = _load()
    for i in range(55):
        _write(cache, f"phrase number {i}.", np.zeros(10, dtype=np.float32), 24000)
    # A real phrase, and a "phrase number" sentence that holds real sound.
    _write(cache, "Opening.", np.ones(24000, dtype=np.float32) * 0.1, 24000)
    _write(cache, "phrase number 3.", np.zeros(10, dtype=np.float32), 24000)
    real = cache._cache_path(cache._cache_key("phrase number 7."))
    np.savez_compressed(real, audio=np.ones(48000, dtype=np.float32) * 0.1, rate=24000)

    found = module.find(cache)
    assert len(found) == 54, "a real render was listed, or a stub was missed"
    assert real not in found
    assert cache._cache_path(cache._cache_key("Opening.")) not in found


def test_nothing_is_deleted_without_a_typed_yes(cache, monkeypatch):
    module = _load()
    _write(cache, "phrase number 0.", np.zeros(10, dtype=np.float32), 24000)
    monkeypatch.setattr(module, "find", lambda speaker=None: module.__dict__["_found"])
    module._found = [cache._cache_path(cache._cache_key("phrase number 0."))]
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert module.main(["--delete"]) == 1
    assert module._found[0].exists()
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    assert module.main(["--delete"]) == 0
    assert not module._found[0].exists()


def test_listing_alone_deletes_nothing(cache, monkeypatch):
    module = _load()
    _write(cache, "phrase number 0.", np.zeros(10, dtype=np.float32), 24000)
    path = cache._cache_path(cache._cache_key("phrase number 0."))
    monkeypatch.setattr(module, "find", lambda speaker=None: [path])
    assert module.main([]) == 0
    assert path.exists()
