"""
Chrome Native Messaging's wire format: a 4-byte length, then that many bytes.

Chrome frames every message to and from a native host as an unsigned 32-bit
length header in NATIVE byte order, followed by exactly that many bytes of
UTF-8 JSON. Get the header wrong - big-endian instead of native, or the byte
count off by one - and the host and Chrome silently talk past each other,
which is the single most common way a native-messaging integration fails
mute. So the framing lives here, alone, with its own tests, rather than
inline in the host loop where a mistake would be invisible.

Chrome also caps a single message at 1 MB inbound to the host and 64 MB out.
The read side enforces the inbound cap: an oversized header is a corrupt or
hostile stream, not a real message, and reading a gigabyte because a browser
said to is how a relay becomes a denial of service.
"""
from __future__ import annotations

import json
import struct
from typing import Any, BinaryIO

# Chrome's documented limit for a message SENT TO a native host.
MAX_MESSAGE_BYTES = 1024 * 1024


class FramingError(ValueError):
    """A length header or body that does not obey Chrome's format."""


def encode(message: Any) -> bytes:
    """
    One message as Chrome expects it on the wire.

    Native byte order for the header, matching what Chrome writes and reads;
    `struct.pack("=I", ...)` is that, explicitly, rather than the ambiguous
    default alignment of no prefix.
    """
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return struct.pack("=I", len(body)) + body


def write_message(stream: BinaryIO, message: Any) -> None:
    stream.write(encode(message))
    stream.flush()


def read_message(stream: BinaryIO) -> "dict | None":
    """
    The next message, or None at end of stream (Chrome closed the port).

    None specifically means "the pipe ended cleanly" - the host's read loop
    treats that as "Chrome is gone, shut down", which is a normal event, not
    an error. A truncated header or an oversized body IS an error and raises.
    """
    header = _read_exactly(stream, 4)
    if header is None:
        return None                      # clean EOF between messages
    if len(header) < 4:
        raise FramingError("stream ended inside a length header")
    (length,) = struct.unpack("=I", header)
    if length == 0:
        return None
    if length > MAX_MESSAGE_BYTES:
        raise FramingError(
            f"message claims {length} bytes, over the {MAX_MESSAGE_BYTES} cap")
    body = _read_exactly(stream, length)
    if body is None or len(body) < length:
        raise FramingError("stream ended inside a message body")
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise FramingError(f"body is not valid JSON: {exc}") from exc


def _read_exactly(stream: BinaryIO, count: int) -> "bytes | None":
    """
    Exactly `count` bytes, or None at a clean EOF before any were read.

    A pipe read can return fewer bytes than asked even when more are coming,
    so a single .read(count) is a latent, load-dependent bug. This loops
    until it has them all or the stream ends.
    """
    chunks = []
    got = 0
    while got < count:
        chunk = stream.read(count - got)
        if not chunk:
            return b"".join(chunks) if chunks else None
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)
