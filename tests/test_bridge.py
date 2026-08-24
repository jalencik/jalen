"""
The Python spine of the Chrome-extension bridge: protocol, framing, server.

These are the seams a native-messaging integration fails at silently - a
wrong-endian length header, a partial pipe read, a token that isn't checked -
so they are tested here in isolation, without a browser. The end-to-end path
through real Chrome needs the user to load the unpacked extension and cannot
be driven headlessly; what CAN be proven without Chrome is proven here.
"""
from __future__ import annotations

import io
import socket
import struct
import threading
import time

import pytest

from jarvis.bridge import framing, protocol
from jarvis.bridge.server import BridgeServer, BridgeError


# ---------------------------------------------------------------------------
# PROTOCOL
# ---------------------------------------------------------------------------
class TestProtocol:

    def test_a_command_round_trips(self):
        cmd = protocol.build_command("get_page_state", {"tab": 3})
        parsed = protocol.parse_command(cmd)
        assert parsed["command"] == "get_page_state"
        assert parsed["payload"] == {"tab": 3}
        assert parsed["version"] == protocol.PROTOCOL_VERSION

    def test_an_unknown_command_is_refused_at_build(self):
        with pytest.raises(protocol.ProtocolError):
            protocol.build_command("run_arbitrary_js")

    def test_arbitrary_js_is_not_in_the_allowlist(self):
        """The brain must not be able to run any script in his logged-in Chrome."""
        assert "eval" not in protocol.COMMANDS
        assert "run_js" not in protocol.COMMANDS
        assert "execute_script" not in protocol.COMMANDS

    def test_a_response_carries_its_request_id(self):
        r = protocol.build_response("abc123", {"title": "x"})
        assert protocol.parse_incoming(r)["request_id"] == "abc123"

    def test_a_wrong_version_is_rejected(self):
        bad = protocol.build_command("ping")
        bad["version"] = 999
        with pytest.raises(protocol.ProtocolError):
            protocol.parse_command(bad)

    def test_a_malformed_request_id_is_rejected(self):
        bad = protocol.build_command("ping")
        bad["request_id"] = "no spaces or slashes/allowed"
        with pytest.raises(protocol.ProtocolError):
            protocol.parse_command(bad)

    def test_the_app_rejects_an_unsolicited_command_from_the_extension(self):
        """
        The safety asymmetry: the extension may answer and may raise events,
        but it may not ORIGINATE a command. parse_incoming is the app's front
        door and it accepts only responses and events.
        """
        cmd = protocol.build_command("click", {"selector": "#x"})
        with pytest.raises(protocol.ProtocolError):
            protocol.parse_incoming(cmd)

    def test_an_unknown_event_is_rejected(self):
        evt = protocol.build_event("heartbeat")
        evt["event"] = "please_type_this_secret"
        with pytest.raises(protocol.ProtocolError):
            protocol.parse_incoming(evt)

    def test_an_error_response_needs_a_code(self):
        err = protocol.build_error("abc123", "PAGE_UNAVAILABLE", "gone")
        assert protocol.parse_incoming(err)["error"]["code"] == "PAGE_UNAVAILABLE"

    def test_show_message_is_an_app_originated_command(self):
        """The app speaks into its own panel; the page cannot."""
        cmd = protocol.build_command("show_message",
                                     {"role": "jalen", "text": "on it"})
        assert protocol.parse_command(cmd)["command"] == "show_message"

    def test_user_message_is_an_event_not_a_command(self):
        """
        A line the user typed in the panel arrives as an EVENT - untrusted
        input the app considers - never as a command the app must run.
        """
        evt = protocol.build_event("user_message", {"text": "hi"})
        assert protocol.parse_incoming(evt)["event"] == "user_message"
        # And it may not masquerade as a command into the app.
        fake = protocol.build_event("user_message", {"text": "x"})
        fake["type"] = "command"
        fake["command"] = "user_message"
        with pytest.raises(protocol.ProtocolError):
            protocol.parse_command(fake)


# ---------------------------------------------------------------------------
# FRAMING
# ---------------------------------------------------------------------------
class TestFraming:

    def test_encode_uses_a_native_uint32_header(self):
        raw = framing.encode({"a": 1})
        (length,) = struct.unpack("=I", raw[:4])
        assert length == len(raw) - 4

    def test_a_message_round_trips_through_a_stream(self):
        msg = {"version": 1, "hello": "world", "n": 42}
        stream = io.BytesIO(framing.encode(msg))
        assert framing.read_message(stream) == msg

    def test_two_messages_back_to_back(self):
        stream = io.BytesIO(framing.encode({"n": 1}) + framing.encode({"n": 2}))
        assert framing.read_message(stream)["n"] == 1
        assert framing.read_message(stream)["n"] == 2
        assert framing.read_message(stream) is None

    def test_a_clean_eof_returns_none(self):
        assert framing.read_message(io.BytesIO(b"")) is None

    def test_a_truncated_header_raises(self):
        with pytest.raises(framing.FramingError):
            framing.read_message(io.BytesIO(b"\x02\x00"))     # 2 of 4 bytes

    def test_a_truncated_body_raises(self):
        header = struct.pack("=I", 100)
        with pytest.raises(framing.FramingError):
            framing.read_message(io.BytesIO(header + b"only ten.."))

    def test_an_oversized_message_is_refused_not_read(self):
        header = struct.pack("=I", framing.MAX_MESSAGE_BYTES + 1)
        with pytest.raises(framing.FramingError):
            framing.read_message(io.BytesIO(header + b"x"))

    def test_a_partial_read_still_assembles_the_whole_message(self):
        """
        A pipe can hand back fewer bytes than asked. A one-shot read() would
        drop the tail; _read_exactly must loop.
        """
        class Dribble:
            def __init__(self, data):
                self._data = data
                self._i = 0

            def read(self, n):
                chunk = self._data[self._i:self._i + 1]   # one byte at a time
                self._i += len(chunk)
                return chunk

        msg = {"a": "some longer payload that spans many reads", "b": [1, 2, 3]}
        assert framing.read_message(Dribble(framing.encode(msg))) == msg


# ---------------------------------------------------------------------------
# SERVER, with a stand-in relay (no browser)
# ---------------------------------------------------------------------------
class _Relay:
    """
    Plays the native host: connects, authenticates, and answers commands the
    way the extension would - so the server's request/response machinery is
    exercised for real over a real loopback socket.
    """

    def __init__(self, port, token, answer=None):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.sendall(framing.encode({"token": token}))
        self._answer = answer or (lambda cmd: {"echo": cmd["command"]})
        self._reader = self.sock.makefile("rb")
        self.received = []

    def serve_one(self):
        msg = framing.read_message(self._reader)
        if msg is None:
            return None
        self.received.append(msg)
        result = self._answer(msg)
        if result is not None:
            self.sock.sendall(framing.encode(
                protocol.build_response(msg["request_id"], result)))
        return msg

    def send_event(self, event, payload=None):
        self.sock.sendall(framing.encode(protocol.build_event(event, payload)))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


@pytest.fixture()
def server():
    srv = BridgeServer()
    srv.start()
    yield srv
    srv.stop()


class TestServer:

    def _wait_connected(self, server, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if server.connected:
                return
            time.sleep(0.02)
        raise AssertionError("relay never registered as connected")

    def test_a_command_gets_its_response(self, server):
        relay = _Relay(server._port, server._token)
        self._wait_connected(server)
        threading.Thread(target=relay.serve_one, daemon=True).start()
        result = server.send_command("get_page_state", timeout=5)
        assert result == {"echo": "get_page_state"}
        relay.close()

    def test_no_connection_is_a_clear_error_not_a_hang(self, server):
        with pytest.raises(BridgeError, match="isn't connected"):
            server.send_command("ping", timeout=1)

    def test_a_wrong_token_is_refused(self, server):
        bad = socket.create_connection(("127.0.0.1", server._port), timeout=5)
        bad.sendall(framing.encode({"token": "not-the-token"}))
        time.sleep(0.3)
        assert not server.connected, "a bad token was let in"
        bad.close()

    def test_a_timeout_is_reported(self, server):
        relay = _Relay(server._port, server._token,
                       answer=lambda cmd: None)   # never answers
        self._wait_connected(server)
        threading.Thread(target=relay.serve_one, daemon=True).start()
        with pytest.raises(BridgeError, match="didn't answer"):
            server.send_command("ping", timeout=0.5)
        relay.close()

    def test_an_event_reaches_the_handler(self, server):
        seen = []
        server.on_event(lambda msg: seen.append(msg["event"]))
        relay = _Relay(server._port, server._token)
        self._wait_connected(server)
        relay.send_event("tab_updated", {"tabId": 5})
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not seen:
            time.sleep(0.02)
        assert seen == ["tab_updated"]
        relay.close()

    def test_the_bridge_file_carries_the_port_and_token(self, server):
        import json
        from jarvis.bridge.server import BRIDGE_FILE
        info = json.loads(BRIDGE_FILE.read_text(encoding="utf-8"))
        assert info["port"] == server._port
        assert len(info["token"]) >= 16, "the token is too short to be a secret"


class TestNativeHostConnectsForReal:
    """
    The native host - the process Chrome launches - reads bridge.json and
    authenticates against the live server. This drives that exact code path
    (minus Chrome's stdio, which test_framing covers) so the two halves are
    proven to meet.
    """

    def test_the_host_connects_and_authenticates(self):
        from jarvis.bridge import native_host, framing, protocol
        srv = BridgeServer()
        srv.start()
        try:
            sock = native_host._connect_to_app()   # reads the real bridge.json
            assert sock is not None, "the host couldn't reach the server"
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not srv.connected:
                time.sleep(0.02)
            assert srv.connected, "the server never saw the host authenticate"

            # And a command actually reaches the host's socket and a response
            # comes back - the full app<->host loop, one frame each way.
            reader = sock.makefile("rb")
            got = {}

            def answer():
                cmd = framing.read_message(reader)
                got["command"] = cmd["command"]
                sock.sendall(framing.encode(
                    protocol.build_response(cmd["request_id"], {"pong": True})))

            threading.Thread(target=answer, daemon=True).start()
            result = srv.send_command("ping", timeout=5)
            assert result == {"pong": True}
            assert got["command"] == "ping"
            sock.close()
        finally:
            srv.stop()

    def test_the_host_gives_up_cleanly_when_the_app_is_absent(self, monkeypatch, tmp_path):
        from jarvis.bridge import native_host
        # Point it at a bridge.json that does not exist.
        monkeypatch.setattr(native_host, "BRIDGE_FILE", tmp_path / "absent.json")
        assert native_host._connect_to_app() is None
