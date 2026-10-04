"""
The test suite was still writing into his real data/router_misses.log.

That log is meant to be read weekly to decide which of HIS phrasings deserve
a rule (router.py's own header), and tests/conftest.py redirects the router's
DATA_DIR for exactly that reason. But IntentRouter.__init__ captured
DATA_DIR / "router_misses.log" when the router was BUILT, and
tests/test_conversation_requests.py builds one at import time - during
collection, before any session fixture runs. So its routes("jaluddin") miss
went into the real file on every run: 227 of its 10,719 lines were exactly
"jaluddin" on 2026-10-01, and a snapshot of data/ around a full gate showed
it was the one real file the suite still changed.

The path is now resolved when a miss is WRITTEN, so no construction order
can escape the redirect.
"""
from __future__ import annotations

from jalen.brain import router as router_module
from jalen.brain.router import IntentRouter
from jalen.config import CONFIG


def test_a_router_built_before_the_redirect_still_writes_where_data_dir_points(monkeypatch, tmp_path):
    built_early = IntentRouter(CONFIG)
    monkeypatch.setattr(router_module, "DATA_DIR", tmp_path)
    built_early.route("zzqx a sentence no rule could ever match")
    log = tmp_path / "router_misses.log"
    assert log.exists(), "the miss went to the path captured at construction"
    assert "zzqx a sentence" in log.read_text(encoding="utf-8")
