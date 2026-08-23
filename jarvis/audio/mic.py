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
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.blocksize,
            device=self.device,
            callback=self._callback,
            latency="low",
        )
        self._stream.start()
        self._running.set()
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
                continue

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
