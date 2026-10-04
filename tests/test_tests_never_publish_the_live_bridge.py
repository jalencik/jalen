"""
The suite still writes and deletes the REAL data/bridge.json.

That file tells the Jalen extension in his everyday Chrome which server to
talk to. server.py:59-64 records what happens when a test publishes over it:
the extension follows the test server, and when the test's srv.stop()
deletes the file, his browser has no link until Jalen restarts.

Found 2026-10-01 on main 7d4be52 - three writers, none redirected by
conftest.py (which redirects audit, crash log, memory, vault and browser
profile, but nothing under jalen.bridge):

  1. test_bridge.py::test_the_host_connects_and_authenticates builds
     BridgeServer() on the real file "ON PURPOSE ... Restored after" - but
     srv.stop() unlinks it; nothing restores a live Jalen's copy.
  2. test_native_host_process.py:196-198 unlinks the real file.
  3. Every test that builds a real Jalen() starts get_server() on the real
     file from Jalen.__init__ - 8 sites, e.g. test_brain_loop.py:76.

A spawned native host reads JALEN_BRIDGE_FILE or else the real file, so a
module patch alone does not cover the subprocess tests.

These tests check the redirect, not the damage: they never start a server.
FAILS ON MAIN BY DESIGN - conftest and the bridge are the Integrator's and
Wave 4 browser-everyday's to change (docs/collab/outbox/collab-browser.md).
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE = (ROOT / "data" / "bridge.json").resolve()


def test_a_server_built_with_defaults_does_not_advertise_on_the_live_file():
    from jalen.bridge.server import BridgeServer
    srv = BridgeServer()   # __init__ only records the path; nothing is bound
    assert Path(srv._bridge_file).resolve() != LIVE


def test_the_native_host_module_does_not_read_the_live_file():
    from jalen.bridge import native_host
    assert Path(native_host.BRIDGE_FILE).resolve() != LIVE


def test_a_host_the_suite_spawns_is_pointed_away_from_the_live_file():
    where = os.environ.get("JALEN_BRIDGE_FILE")
    assert where, "a native host spawned by a test would read the live file"
    assert Path(where).resolve() != LIVE
