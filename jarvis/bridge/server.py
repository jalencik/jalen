"""
The app's end of the bridge: where authority over the extension lives.

The Jalen app runs a loopback server. The native host - the little process
Chrome spawns for the extension - connects to it as a client, proves it
holds a shared token, and then relays frames between Chrome and here. So
this class is the ONE place the app talks to his browser, and the one place
that decides what his browser is allowed to do.

WHY A LOOPBACK SOCKET, AND WHY IT IS SAFE
-----------------------------------------
A native-messaging host is a fresh subprocess per connection; it cannot see
into the long-running app any other way. The socket is bound to 127.0.0.1
only - never a routable address - and the first frame must carry a random
per-run token written to a file only this user can read. A process that
cannot read that file cannot speak to the app, so "there is a localhost
port" is not "anyone local can drive his browser".

WHAT DOES NOT HAPPEN HERE
-------------------------
The server does not decide that an action is safe. It carries commands the
rest of the app has already classified, and returns what the extension saw.
The page on the other end can answer a command and can raise an event; it
cannot originate a command, because commands are built and sent only from
the app side. That asymmetry is the safety boundary.
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import framing, protocol

ROOT = Path(__file__).resolve().parent.parent.parent
BRIDGE_FILE = ROOT / "data" / "bridge.json"


class BridgeError(RuntimeError):
    """The extension is not connected, or a command could not be delivered."""


class BridgeServer:
    """
    One loopback server, one connected relay at a time.

    Only one Chrome is his Chrome, so only one relay connects; a second is
    accepted and immediately replaces the first, which is what happens when
    Chrome restarts the host after a reconnect.
    """

    def __init__(self, bridge_file: "Path | None" = None) -> None:
        # WHICH FILE ADVERTISES THIS SERVER. Overridable because a test that
        # writes the real data/bridge.json does not merely fail to isolate -
        # it STEALS HIS BROWSER. Observed exactly that: a unit test started a
        # server, published itself over the live file, and the extension
        # running in his Chrome dutifully reconnected to the test. Tests pass
        # a temp path; only the app uses the real one.
        self._bridge_file = bridge_file or BRIDGE_FILE
        self._sock: socket.socket | None = None
        self._conn: socket.socket | None = None
        self._token = secrets.token_hex(16)
        self._port = 0
        self._pending: dict[str, queue.Queue] = {}
        self._lock = threading.Lock()
        self._on_event: Callable[[dict], None] | None = None
        self._running = False
        self._conn_lock = threading.Lock()

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Bind, publish the port+token, and accept relays in the background."""
        if self._running:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))     # loopback, ephemeral port
        self._sock.listen(1)
        self._port = self._sock.getsockname()[1]
        self._running = True
        self._publish()
        threading.Thread(target=self._accept_loop,
                         name="jalen-bridge-accept", daemon=True).start()

    def _publish(self) -> None:
        """
        Write port+token where the native host will read them, readable by
        this user only. The token is a secret in the same sense a password
        is - anything that can read it can drive his browser - so it is
        written with the same care.
        """
        self._bridge_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"port": self._port, "token": self._token,
                   "pid": os.getpid(), "at": time.time()}
        tmp = self._bridge_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass                              # best effort on Windows
        tmp.replace(self._bridge_file)

    def stop(self) -> None:
        self._running = False
        for sock in (self._conn, self._sock):
            try:
                if sock:
                    sock.close()
            except OSError:
                pass
        self._conn = None
        self._sock = None
        try:
            self._bridge_file.unlink()
        except OSError:
            pass

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def on_event(self, callback: Callable[[dict], None]) -> None:
        self._on_event = callback

    # ------------------------------------------------------------------ accept
    def _accept_loop(self) -> None:
        while self._running and self._sock is not None:
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                break
            if not self._authenticate(conn):
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            with self._conn_lock:
                old = self._conn
                self._conn = conn
            if old is not None:
                try:
                    old.close()
                except OSError:
                    pass
            threading.Thread(target=self._read_loop, args=(conn,),
                             name="jalen-bridge-read", daemon=True).start()

    def _authenticate(self, conn: socket.socket) -> bool:
        """
        First frame must be {"token": <the published token>}. A constant-time
        compare, because a token check that leaks timing is a token check
        that can be guessed.
        """
        try:
            conn.settimeout(10.0)
            reader = conn.makefile("rb")
            first = framing.read_message(reader)
            conn.settimeout(None)
        except (OSError, framing.FramingError):
            return False
        if not isinstance(first, dict):
            return False
        offered = first.get("token", "")
        return isinstance(offered, str) and secrets.compare_digest(
            offered, self._token)

    # -------------------------------------------------------------------- read
    def _read_loop(self, conn: socket.socket) -> None:
        reader = conn.makefile("rb")
        while self._running:
            try:
                raw = framing.read_message(reader)
            except (OSError, framing.FramingError):
                break
            if raw is None:
                break
            try:
                msg = protocol.parse_incoming(raw)
            except protocol.ProtocolError:
                continue                      # a bad frame is dropped, not fatal
            if msg["type"] == "response":
                self._resolve(msg)
            elif msg["type"] == "event":
                self._dispatch_event(msg)
        with self._conn_lock:
            if self._conn is conn:
                self._conn = None

    def _resolve(self, msg: dict) -> None:
        with self._lock:
            waiter = self._pending.pop(msg["request_id"], None)
        if waiter is not None:
            waiter.put(msg)

    def _dispatch_event(self, msg: dict) -> None:
        if self._on_event is not None:
            try:
                self._on_event(msg)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ command
    def wait_connected(self, timeout: float = 12.0) -> bool:
        """
        Wait for the extension to be there. True if it is, within timeout.

        WHY THIS EXISTS, measured over four minutes on his machine: the link
        was up in 20 of 24 samples, with short gaps that healed themselves
        within about five seconds every time. That is not a fault - it is
        MV3's service-worker lifecycle. Chrome sleeps an idle worker, the
        native port closes with it, and the extension's alarm brings it
        straight back.

        But a caller that happens to land in one of those gaps would get
        "not connected" for a browser that is, in every meaningful sense,
        connected. Waiting a few seconds turns an 83%-available link into a
        reliable one, and costs nothing when the link is already up.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._conn is not None:
                return True
            time.sleep(0.25)
        return self._conn is not None

    def send_command(self, command: str, payload: dict | None = None,
                     timeout: float = 20.0) -> Any:
        """
        Send a command and BLOCK for its response. Returns the result, or
        raises BridgeError - including, deliberately, when nothing is
        connected, so a caller never mistakes "no browser" for "empty page".

        Waits through a service-worker gap first; see wait_connected.
        """
        conn = self._conn
        if conn is None:
            # Bounded by the CALLER's patience, not a fixed window: someone
            # who passes timeout=1 wants an answer in about a second, and a
            # helpful wait that overruns it is just a hang with good manners.
            self.wait_connected(min(12.0, max(0.0, timeout)))
            conn = self._conn
        if conn is None:
            raise BridgeError(
                "Jalen's Chrome extension isn't connected. Open Chrome with "
                "the extension installed, or use the other browser route.")
        message = protocol.build_command(command, payload)
        rid = message["request_id"]
        waiter: queue.Queue = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[rid] = waiter
        try:
            conn.sendall(framing.encode(message))
        except OSError as exc:
            with self._lock:
                self._pending.pop(rid, None)
            raise BridgeError(f"couldn't reach the extension: {exc}") from exc
        try:
            reply = waiter.get(timeout=timeout)
        except queue.Empty:
            with self._lock:
                self._pending.pop(rid, None)
            raise BridgeError(
                f"the extension didn't answer '{command}' within "
                f"{int(timeout)}s") from None
        if reply.get("ok"):
            return reply.get("result")
        err = reply.get("error", {})
        raise BridgeError(f"{err.get('code','ERROR')}: {err.get('message','')}")


# One per process, like the CDP session. The app starts it; tools use it.
_INSTANCE: BridgeServer | None = None
_INSTANCE_LOCK = threading.Lock()


def get_server() -> BridgeServer:
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = BridgeServer()
        return _INSTANCE
