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
def _no_browser_window_on_his_screen():
    """
    Tests that start Jalen's REAL Chrome run it headless. Without this, every
    test run popped a Chrome window on his screen and killed it a second later
    - see webagent._test_headless_flag and tests/test_tests_never_show_a_browser.py.
    """
    import os

    before = os.environ.get("JALEN_TEST_HEADLESS_CHROME")
    os.environ["JALEN_TEST_HEADLESS_CHROME"] = "1"
    yield
    if before is None:
        os.environ.pop("JALEN_TEST_HEADLESS_CHROME", None)
    else:
        os.environ["JALEN_TEST_HEADLESS_CHROME"] = before


@pytest.fixture(autouse=True)
def _every_test_starts_and_ends_untainted():
    """
    Taint is process-wide (jarvis/taint.py), which is right for Jalen - one
    turn, one origin - and wrong for a test run, where it leaked from one
    FILE into the next. tests/test_web_sign_in.py resumes a delegation that
    reads a web answer, and left the whole process at origin_now() ==
    "content". Every safety test after it then classified as if Jalen had
    just read a stranger's page: test_agent_hook_safety_gate's confirmed
    delete_file was refused, but only when run after it - the full gate's
    alphabetical order hid it. Worse is the other direction: a test that
    asserts a refusal could pass because of taint some earlier file left,
    not because of the rule it is testing.
    """
    from jarvis import taint

    taint.he_asked_again()
    yield
    taint.he_asked_again()


@pytest.fixture(autouse=True)
def _every_test_starts_with_no_form_read():
    """
    The form inspect_form last read (webforms._FORM_READ) is process-wide,
    the same shape as taint above: left set by one file, it would let a
    later test's fill act on a form that test never read - and a test that
    asserts "nothing read, nothing filled" pass or fail by file order.
    """
    from jarvis.tools import webforms

    webforms._forget_the_form()
    yield
    webforms._forget_the_form()


@pytest.fixture(scope="session", autouse=True)
def _no_test_walks_a_real_drive(tmp_path_factory):
    """
    disk_report() counts a whole drive from its root: measured 131 s and 216 s
    on this C: in two runs, a million files listed. tests/test_sysinfo_tools.py calls it against
    "the real machine", so without this every test run would walk his C: in the
    background, and the walk would still be going when the suite ended. The one
    seam, sysinfo._scan_target, points every scan at a small scratch folder.
    Tests of the walk itself build their own trees and call DriveScan directly.
    """
    from jarvis.tools import sysinfo

    fake = tmp_path_factory.mktemp("jarvis-fake-drive")
    (fake / "Users" / "pretend" / "Documents").mkdir(parents=True)
    (fake / "Users" / "pretend" / "Documents" / "notes.txt").write_bytes(b"x" * 2048)
    (fake / "Program Files").mkdir()
    (fake / "Program Files" / "tool.bin").write_bytes(b"x" * 4096)

    saved = sysinfo._scan_target
    sysinfo._scan_target = lambda root: str(fake)
    try:
        yield fake
    finally:
        sysinfo._scan_target = saved


@pytest.fixture(autouse=True)
def _every_test_starts_and_ends_with_no_drive_scan():
    """
    The scans disk_report keeps (sysinfo._slots) are process-wide, like taint:
    one test's finished scan would be served to the next test as "recent", and
    one test's fake scan would answer another's real ask, by file order. The
    cleanup report's own cache (sysinfo._cache) is the same shape: a test that
    stubbed the report left its stub as the "recent answer" for the next one.
    """
    from jarvis.tools import sysinfo

    def forget() -> None:
        sysinfo._forget_scans()
        with sysinfo._cache_lock:
            sysinfo._cache.clear()

    forget()
    yield
    forget()


@pytest.fixture(scope="session", autouse=True)
def _keep_tests_out_of_real_data(tmp_path_factory):
    scratch = tmp_path_factory.mktemp("jarvis-test-data")

    from jarvis import audit as audit_module
    from jarvis import crashlog as crashlog_module
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

    # --- crash log and exit state -----------------------------------------
    # Same reasoning as the audit log, and with a sharper edge. Three tests
    # construct a real Jalen, and Jalen.__init__ calls crashlog.mark_running()
    # — which writes data/last_exit.json saying a run is IN PROGRESS. Those
    # test instances are never shut down, so the file would still say
    # "running" when the suite ends, and the next real start would report
    # "previous run ended WITHOUT shutting down" about a pytest process. The
    # one line that exists to flag a genuine silent exit would be a false
    # alarm after every test run, which is exactly how a useful signal gets
    # trained out of someone.
    saved_crash = (
        crashlog_module.DATA_DIR,
        crashlog_module.CRASH_LOG,
        crashlog_module.EXIT_STATE,
    )
    crashlog_module.DATA_DIR = scratch
    crashlog_module.CRASH_LOG = scratch / "crash.log"
    crashlog_module.EXIT_STATE = scratch / "last_exit.json"

    # --- Jalen's own Chrome: delegations and the profile ------------------
    # web_delegate saves every completed delegation to CHATS_PATH, and
    # tests/test_web_sign_in.py completes a fake one. Measured 2026-09-30:
    # all 68 records in the real data/web_chats.json were that fixture, so
    # list_web_chats and read_web_result("") told him about a test. The
    # real-Chrome tests (test_webforms, test_cdp_browser, ...) also ran on
    # PROFILE_DIR - his signed-in data/browser_profile - and _launch first
    # ends any chrome.exe holding that profile, which is his live one.
    from jarvis.tools import webagent as webagent_module

    saved_webagent = (webagent_module.CHATS_PATH, webagent_module.PROFILE_DIR)
    webagent_module.CHATS_PATH = scratch / "web_chats.json"
    webagent_module.PROFILE_DIR = scratch / "browser_profile"

    # --- the vault, its site approvals and which site each secret is for --
    # Most vault tests point these at tmp_path themselves; this is for the
    # ones that do not. A test that ties "gmail" to a site, or approves one,
    # must never write that into his real data/, and a secret typed by a
    # fixture must never come out of his real vault.
    from jarvis.tools import vault as vault_module

    saved_vault = (vault_module.VAULT_PATH, vault_module.APPROVALS_PATH,
                   vault_module.SECRET_SITES_PATH)
    vault_module.VAULT_PATH = scratch / "vault.json"
    vault_module.APPROVALS_PATH = scratch / "site_approvals.json"
    vault_module.SECRET_SITES_PATH = scratch / "secret_sites.json"

    try:
        yield scratch
    finally:
        (vault_module.VAULT_PATH, vault_module.APPROVALS_PATH,
         vault_module.SECRET_SITES_PATH) = saved_vault
        webagent_module.CHATS_PATH, webagent_module.PROFILE_DIR = saved_webagent
        (
            crashlog_module.DATA_DIR,
            crashlog_module.CRASH_LOG,
            crashlog_module.EXIT_STATE,
        ) = saved_crash
        audit_cfg.clear()
        audit_cfg.update(saved_audit)
        memory_cfg.clear()
        memory_cfg.update(saved_memory)
        router_module.DATA_DIR = saved_data_dir
        if saved_audit_root is not None:
            audit_module.DATA_DIR = saved_audit_root
