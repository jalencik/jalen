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
from typing import Iterator

import numpy as np

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - lets the rest import on non-Windows CI
    sd = None


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

    def stop(self) -> None:
        self._running.clear()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    def frames(self, timeout: float = 1.0) -> Iterator[np.ndarray]:
        """Yield float32 mono frames of `blocksize` samples until stopped."""
        while self._running.is_set():
            try:
                yield self._q.get(timeout=timeout)
            except queue.Empty:
                continue

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
