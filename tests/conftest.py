"""
Keep the test suite out of the real data directory.

Every round of work on this project has been driven by reading
data/audit.jsonl — the 2000ms endpointing tax, the 30-second noise hang,
the "I didn't catch that" spam, the backspace regex. That log is the
diagnostic instrument, and the test suite was writing into it.

The evidence, from the live log on 2026-08-21, interleaved with his real
Telegram messages:

    04:12:05  action  YOU  transfer funds
    04:12:05  action  YOU  delete file: C:/tmp/x.txt
    04:12:24  action  YOU  some undefined amber tool xyz: notepad
    04:17:13  utterance YOU  What time is it?          <- actually him
    04:17:41  utterance YOU  ...open telegram in my desktop

Three problems with that, in increasing order of seriousness:

1. It skews the measurements. `delete_file` appeared 169 times and
   `transfer_funds` 154 times in a log of 738 actions — none of them real.
   Any "what does he actually do" analysis is reading mostly test fixtures.
2. It buries the real records. The signal is diluted by an order of
   magnitude every time the suite runs.
3. It makes a SECURITY record untrustworthy. audit.jsonl exists so he can
   answer "what did Jarvis do yesterday?" — and a genuine refused
   `transfer_funds` attempt is now indistinguishable from a fixture. A log
   you have to second-guess is not an audit trail.

AuditLog resolves its paths from CONFIG at construction time, so pointing
those two keys at a temp directory for the duration of the session moves
every AuditLog the tests build, without any test needing to know. Same for
the memory database and the router's miss log, which were being written
for the same reason.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _keep_tests_out_of_real_data(tmp_path_factory):
    scratch = tmp_path_factory.mktemp("jarvis-test-data")

    from jarvis import audit as audit_module
    from jarvis.brain import router as router_module
    from jarvis.config import CONFIG

    # --- audit sinks -------------------------------------------------------
    # Absolute paths on purpose: AuditLog does `root / cfg_value`, and
    # pathlib's join returns the right-hand side unchanged when it is
    # already absolute. A relative value would land back under the repo.
    audit_cfg = CONFIG.setdefault("audit", {})
    saved_audit = dict(audit_cfg)
    audit_cfg["db_path"] = str(scratch / "audit.db")
    audit_cfg["jsonl_path"] = str(scratch / "audit.jsonl")

    # --- memory database ---------------------------------------------------
    memory_cfg = CONFIG.setdefault("memory", {})
    saved_memory = dict(memory_cfg)
    memory_cfg["db_path"] = str(scratch / "jarvis.db")

    # --- router miss log ---------------------------------------------------
    # router.py writes unmatched phrases to DATA_DIR/router_misses.log, and
    # that file is meant to be read weekly to decide which phrasings deserve
    # a rule. Test phrases in it would send that work chasing sentences
    # nobody ever said.
    saved_data_dir = router_module.DATA_DIR
    router_module.DATA_DIR = scratch

    # --- module-level defaults --------------------------------------------
    # audit.py resolves paths per instance, but pin the module's own notion
    # of the data directory too if it has one, so nothing slips through.
    saved_audit_root = getattr(audit_module, "DATA_DIR", None)
    if saved_audit_root is not None:
        audit_module.DATA_DIR = scratch

    try:
        yield scratch
    finally:
        audit_cfg.clear()
        audit_cfg.update(saved_audit)
        memory_cfg.clear()
        memory_cfg.update(saved_memory)
        router_module.DATA_DIR = saved_data_dir
        if saved_audit_root is not None:
            audit_module.DATA_DIR = saved_audit_root
