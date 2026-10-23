"""
Microphone capture. One stream, many consumers.

sounddevice, not pyaudio: pyaudio's last release was Nov 2023 and it has no
wheels for current Pythons. sounddevice bundles PortAudio and shipped
yesterday.

Rule: the audio callback does NOTHING but push bytes into a queue. Inference in
a PortAudio callback causes dropouts.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Iterator

import numpy as np

from .. import crashlog

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - lets the rest import on non-Windows CI
    sd = None

# How long the device may deliver nothing before it is worth writing down.
#
# PortAudio does not raise when a capture device stops producing — a USB
# headset unplugged mid-session, a driver reset, an exclusive-mode grab by
# another application. The callback simply stops being called, frames() sits
# on an empty queue forever, and Jalen goes deaf while looking perfectly
# healthy. Ten seconds is far longer than any real gap between callbacks
# (they arrive every 32 ms) and short enough to catch the stall in the same
# log as whatever happened next.
STALL_WARN_S = 10.0

# AND THEN IT IS REOPENED. Writing the stall down left him with a Jalen that
# was deaf until someone restarted it - "it should not become deaf out of
# nowhere" (2026-10-23). After the same ten seconds of nothing the stream is
# closed and opened again: the configured device first, then the default one
# (a headset that was unplugged is not coming back by itself), and again every
# RETRY_EVERY_S while it stays dead. NOT MEASURED: no stall is in the log with
# a device that came back, so how often a reopen succeeds is unknown; it costs
# nothing when the stream is healthy, because a healthy one is never stalled.
RETRY_EVERY_S = 10.0


class Microphone:
    def __init__(self, cfg) -> None:
        self.sample_rate = int(cfg.get_path("audio.sample_rate", 16000))
        frame_ms = int(cfg.get_path("audio.frame_ms", 32))
        # 32 ms at 16 kHz = 512 samples, which is exactly what Silero VAD wants.
        self.blocksize = int(self.sample_rate * frame_ms / 1000)
        self.device = cfg.get_path("audio.input_device", None)
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._stream = None
        self._running = threading.Event()
        self.dropped = 0
        # Diagnostics. The audio callback may only touch these — it does no
        # I/O and takes no lock, per the module rule above. Reporting is the
        # consumer thread's job, in frames().
        self.status_flags: list[str] = []
        self.last_callback_at: float = 0.0
        self._stall_reported = False
        # Reopening a stream that went quiet (_reopen_if_stalled): how many
        # times, when last tried, and whether he has been told it is down.
        self.reopened = 0
        self._last_reopen_at = 0.0
        self._told_it_is_down = False
        # Set by Jalen.run to something that says a sentence out loud. The
        # microphone is the one part that cannot report its own failure by
        # being heard, so it is told this way instead.
        self.notify = None

    # ----------------------------------------------------------------- device
    @staticmethod
    def list_devices() -> list[dict]:
        if sd is None:
            return []
        out = []
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                out.append(
                    {
                        "index": idx,
                        "name": dev["name"],
                        "channels": dev["max_input_channels"],
                        "default_samplerate": int(dev.get("default_samplerate", 0)),
                    }
                )
        return out

    # ------------------------------------------------------------------- life
    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        # `status` was ignored entirely before. It is PortAudio's only way of
        # saying input overflowed or the device errored, and throwing it away
        # meant a microphone degrading under load looked identical to one
        # working perfectly. Recorded here, reported from frames() — a disk
        # write in an audio callback causes the dropouts it would be
        # describing.
        self.last_callback_at = time.monotonic()
        if status:
            text = str(status)
            if not self.status_flags or self.status_flags[-1] != text:
                # Capped: a device erroring every 32 ms would otherwise grow
                # this list without bound for as long as the process lives.
                if len(self.status_flags) < 50:
                    self.status_flags.append(text)
        try:
            self._q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            self.dropped += 1

    def start(self) -> None:
        if sd is None:
            raise RuntimeError(
                "sounddevice is not installed. Run: pip install sounddevice"
            )
        if self._stream is not None:
            return
        self._open(self.device)
        self._running.set()

    def _open(self, device) -> None:
        """Open and start a capture stream on `device` (None is the default)."""
        stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.blocksize,
            device=device,
            callback=self._callback,
            latency="low",
        )
        stream.start()
        self._stream = stream
        # Start the stall clock at stream-start, not at the first callback.
        # A device that opens successfully and then never delivers a single
        # frame is a real failure — and the one that leaves the least
        # evidence — so it has to be inside the window from the beginning.
        self.last_callback_at = time.monotonic()
        self._stall_reported = False

    def stop(self) -> None:
        self._running.clear()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    def frames(self, timeout: float = 1.0) -> Iterator[np.ndarray]:
        """
        Yield float32 mono frames of `blocksize` samples until stopped.

        Also the only place that notices the device has gone quiet. A capture
        stream that stops delivering does not raise — the callback simply
        stops being called — so without this a deaf Jalen and a listening
        Jalen produce exactly the same (empty) evidence. Reported once per
        stall, not once per second, because the interesting fact is that it
        happened and when, not how long the log can be made.
        """
        while self._running.is_set():
            try:
                yield self._q.get(timeout=timeout)
                self._stall_reported = False
            except queue.Empty:
                self._report_stall()
                self._reopen_if_stalled()
                continue

    def _reopen_if_stalled(self) -> None:
        """
        The device has delivered nothing for STALL_WARN_S: close the stream and
        open it again - the configured device, then the default one - every
        RETRY_EVERY_S until one delivers. He is told once when it is down and
        once when it is back, because a deaf assistant looks exactly like a
        listening one.
        """
        if not self.last_callback_at or sd is None:
            return
        now = time.monotonic()
        if now - self.last_callback_at < STALL_WARN_S or now - self._last_reopen_at < RETRY_EVERY_S:
            return
        self._last_reopen_at = now
        old, self._stream = self._stream, None
        if old is not None:
            try:
                old.stop()
                old.close()
            except Exception as exc:  # noqa: BLE001 - a dead device may refuse to close
                crashlog.write(f"closing the stalled microphone failed: {type(exc).__name__}: {exc}")
        for device in dict.fromkeys([self.device, None]):
            try:
                self._open(device)
            except Exception as exc:  # noqa: BLE001 - try the next one
                crashlog.write(f"reopening the microphone (device={device!r}) failed: "
                               f"{type(exc).__name__}: {exc}")
                continue
            # _open restarted the stall clock, so a device that opens and still
            # delivers nothing is tried again after the same wait.
            self.reopened += 1
            crashlog.write(f"microphone reopened on device={device!r} after a stall "
                           f"(reopen {self.reopened})")
            if self._told_it_is_down:
                self._told_it_is_down = False
                self._tell("My microphone is working again. I can hear you.")
            else:
                self._tell("My microphone dropped out for a moment. It's back now.")
            return
        if not self._told_it_is_down:
            self._told_it_is_down = True
            self._tell("My microphone has stopped working, so I can't hear you. "
                       "Check that it's plugged in. I'll keep trying.")

    def _tell(self, sentence: str) -> None:
        if self.notify is None:
            return
        try:
            self.notify(sentence)
        except Exception:  # noqa: BLE001 - telling him must never take the microphone down
            pass

    def _report_stall(self) -> None:
        if self._stall_reported or not self.last_callback_at:
            return
        idle = time.monotonic() - self.last_callback_at
        if idle < STALL_WARN_S:
            return
        self._stall_reported = True
        crashlog.write(
            f"microphone delivered no audio for {idle:.0f}s "
            f"(device={self.device!r}, dropped={self.dropped}, "
            f"status={self.status_flags[-3:] or 'none'}) - Jalen is deaf but still running"
        )

    def queued_seconds(self) -> float:
        """
        How much un-consumed audio is sitting in the queue. The caller uses
        this to tell "the rest of the sentence you're still saying" (a few
        hundred ms, keep it) from "the machine stalled and frames piled up"
        (stale, drop it) — draining unconditionally destroys the former.
        """
        return self._q.qsize() * self.blocksize / self.sample_rate

    def drain(self) -> None:
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def __enter__(self) -> "Microphone":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
