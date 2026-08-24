"""
The REAL launcher, the REAL host process, the REAL server - everything
except Chrome itself.

Chrome 151 disabled the --load-extension command-line flag, so the one seam
that cannot be driven headlessly is "Chrome loads the extension and its
worker connects". Everything on THIS side of that seam can be, and is here:
the actual jalen_bridge_host.bat is spawned the way Chrome spawns it - from
a foreign working directory - and driven over its stdin/stdout with Chrome's
native-messaging framing. A command sent from the app must come out of the
host's stdout; a response written to its stdin must come back to the app.

This test exists because it would have caught the bug it now guards: the
launcher ran `python -m jarvis.bridge.native_host` without cd-ing to the
project first, so from Chrome's working directory the host died with "No
module named jarvis" and the extension showed "not connected" forever.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from jarvis.bridge import framing, protocol
from jarvis.bridge.server import BridgeServer

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "jalen_bridge_host.bat"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="the .bat launcher is Windows-only")


def _ensure_launcher():
    """Generate the real launcher the installer ships, if it isn't present."""
    if not LAUNCHER.is_file():
        sys.path.insert(0, str(ROOT / "scripts"))
        import install_extension
        install_extension._write_launcher()


@pytest.fixture()
def server():
    srv = BridgeServer()
    srv.start()
    yield srv
    srv.stop()


class TestTheRealHostProcess:

    def test_launcher_cds_to_the_project(self):
        """The fix, asserted in the file itself: without the cd it breaks."""
        _ensure_launcher()
        text = LAUNCHER.read_text(encoding="utf-8")
        assert "cd /d" in text, "the launcher will fail from Chrome's cwd"
        assert str(ROOT) in text

    def test_host_connects_and_relays_from_a_foreign_cwd(self, server):
        """
        Spawn the launcher exactly as Chrome does - from a directory that is
        NOT the project - and run one command round trip through its stdio.
        """
        _ensure_launcher()
        foreign = tempfile.mkdtemp(prefix="not-the-project-")
        proc = subprocess.Popen(
            ["cmd", "/c", str(LAUNCHER)],
            cwd=foreign,                        # the crux: Chrome's cwd, not ours
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            # The host should reach the server within a moment.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not server.connected:
                time.sleep(0.1)
            assert server.connected, (
                "the host never connected - it likely died on startup, which "
                "from a foreign cwd means the module wasn't importable")

            # A command from the app must surface on the host's stdout, and a
            # response written to its stdin must return to the app.
            reply_holder = {}

            def answer_from_chrome():
                msg = framing.read_message(proc.stdout)
                reply_holder["cmd"] = msg
                proc.stdin.write(framing.encode(
                    protocol.build_response(msg["request_id"], {"pong": True})))
                proc.stdin.flush()

            threading.Thread(target=answer_from_chrome, daemon=True).start()
            result = server.send_command("ping", timeout=8)
            assert result == {"pong": True}
            assert reply_holder["cmd"]["command"] == "ping"
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    def test_host_reports_when_the_app_is_absent(self):
        """
        With no server running, the host must emit a valid framed event
        saying so - not crash mutely - so the popup can explain it.
        """
        _ensure_launcher()
        # Point at a non-existent bridge file by running with the real
        # launcher but no server up (BRIDGE_FILE won't exist / is stale).
        from jarvis.bridge.server import BRIDGE_FILE
        if BRIDGE_FILE.exists():
            BRIDGE_FILE.unlink()
        foreign = tempfile.mkdtemp(prefix="no-app-")
        proc = subprocess.Popen(
            ["cmd", "/c", str(LAUNCHER)],
            cwd=foreign, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        try:
            msg = framing.read_message(proc.stdout)
            assert msg is not None
            assert msg["type"] == "event"
            assert msg["event"] == "disconnected"
        finally:
            try:
                proc.terminate(); proc.wait(timeout=5)
            except Exception:
                proc.kill()
