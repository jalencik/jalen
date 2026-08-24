"""
The process Chrome launches. A relay, and nothing more.

Chrome starts this when the extension calls connectNative, and speaks to it
over stdin/stdout in the length-prefixed format framing.py implements. This
process does not think: it reads the running app's port and token from
bridge.json, connects to the app's loopback server, proves the token once,
and then moves whole frames between Chrome and the app in both directions
until either end goes away.

Keeping it dumb is the point. All the judgement - which command is allowed,
what a field means, whether a secret may be typed - lives in the app, behind
the token. This process could be read line by line by anyone and reveal no
capability, because it holds none: it is a wire, not a brain.

BINARY STDIO IS NON-NEGOTIABLE ON WINDOWS. If stdout is in text mode, Windows
translates a lone 0x0A into 0x0D 0x0A, which corrupts the length header and
the body, and Chrome silently disconnects. sys.stdin.buffer / stdout.buffer
are the raw byte streams; msvcrt.setmode makes doubly sure.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
from pathlib import Path

# Runnable both as a module (-m jarvis.bridge.native_host) and as a script
# Chrome launches by path; make the package importable either way.
try:
    from jarvis.bridge import framing
except ImportError:  # pragma: no cover - only when launched by raw path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from jarvis.bridge import framing

ROOT = Path(__file__).resolve().parent.parent.parent

# Which file names the app to connect to. The env var exists so a test can
# point a REAL host process at a REAL but isolated server: without it, a test
# host and the extension running in his live Chrome race for the same
# bridge.json, and whichever connects second displaces the first. Chrome
# never sets this, so normal use is unaffected.
BRIDGE_FILE = Path(os.environ.get("JALEN_BRIDGE_FILE")
                   or (ROOT / "data" / "bridge.json"))


def _binary_stdio():
    """Raw byte stdin/stdout, with CRLF translation forced off on Windows."""
    try:
        import msvcrt
        import os
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    except (ImportError, OSError):
        pass
    return sys.stdin.buffer, sys.stdout.buffer


def _app_is_alive(info: dict) -> bool:
    """
    Is the process that wrote bridge.json still running?

    THE BUG THIS CLOSES, found while diagnosing a "not connected" report.
    bridge.json is written when the app starts and deleted when it stops
    cleanly - but a crash or a kill leaves it behind, pointing at a port
    nothing owns. Ports get REUSED. So the old code would happily connect to
    whatever process later grabbed that port and hand it Jalen's token,
    which is the key to driving his browser.

    Checking the recorded pid is what makes the token safe to send: no live
    writer, no connection, no token. Verified against a real stale file on
    his machine (pid 22820, port 2097, both long dead).
    """
    pid = info.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        import psutil
        return bool(psutil.pid_exists(pid))
    except ImportError:
        # No psutil: fall back to freshness alone rather than assuming alive.
        import time
        try:
            return (time.time() - float(info.get("at", 0))) < 86400
        except (TypeError, ValueError):
            return False
    except Exception:  # noqa: BLE001
        # A corrupt file can hold anything - psutil raises OverflowError on a
        # pid too large for a C long, and this runs in a process whose only
        # job is to relay. Any doubt means DON'T send the token; the cost is
        # a reconnect, and the alternative is handing it to a stranger.
        return False


def _connect_to_app() -> "socket.socket | None":
    """Read the app's port+token and open an authenticated loopback link."""
    try:
        info = json.loads(BRIDGE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # Refuse to speak to a port whose owner is gone. See _app_is_alive.
    if not _app_is_alive(info):
        return None
    try:
        sock = socket.create_connection(("127.0.0.1", int(info["port"])),
                                        timeout=10)
    except (OSError, KeyError, ValueError):
        return None
    try:
        sock.sendall(framing.encode({"token": info.get("token", "")}))
    except OSError:
        sock.close()
        return None
    return sock


def _pump_chrome_to_app(chrome_in, app: socket.socket) -> None:
    """Chrome -> app. Whole frames, unread and unjudged."""
    while True:
        try:
            msg = framing.read_message(chrome_in)
        except framing.FramingError:
            break
        if msg is None:
            break
        try:
            app.sendall(framing.encode(msg))
        except OSError:
            break
    try:
        app.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def _pump_app_to_chrome(app: socket.socket, chrome_out) -> None:
    """app -> Chrome. Whole frames, the other direction."""
    reader = app.makefile("rb")
    while True:
        try:
            msg = framing.read_message(reader)
        except framing.FramingError:
            break
        if msg is None:
            break
        try:
            framing.write_message(chrome_out, msg)
        except OSError:
            break


def main() -> int:
    chrome_in, chrome_out = _binary_stdio()
    app = _connect_to_app()
    if app is None:
        # Tell the extension why, in one valid frame, then exit. The popup
        # turns this into "Jalen isn't running" rather than a mute failure.
        try:
            framing.write_message(chrome_out, {
                "version": 1, "request_id": "bridge-init", "type": "event",
                "event": "disconnected",
                "payload": {"reason": "jalen app not running"}})
        except OSError:
            pass
        return 1

    up = threading.Thread(target=_pump_chrome_to_app, args=(chrome_in, app),
                          daemon=True)
    up.start()
    _pump_app_to_chrome(app, chrome_out)      # blocks until either end closes
    try:
        app.close()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
