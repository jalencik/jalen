"""
The floating orb (spec C21).

tkinter, not PySide6 — deliberately. Qt would look nicer but costs ~90 MB
resident, and on a machine sitting at 86% memory that is a real tax for a
decoration. tkinter ships with Python and the whole overlay costs about 18 MB.
If you later free up RAM and want the prettier version, this is the one file
to replace.

Windows transparency trick: a fully transparent colour key. Anything painted in
CHROMA becomes see-through, so the orb looks like it's floating on the desktop.
"""
from __future__ import annotations

import math
import queue
import threading
import tkinter as tk

CHROMA = "#010203"  # a colour nothing else will use

STATES = ("idle", "listening", "thinking", "speaking", "blocked", "muted")


class Orb:
    def __init__(self, cfg) -> None:
        self.enabled = bool(cfg.get_path("ui.orb", True))
        self.size = int(cfg.get_path("ui.orb_size", 84))
        self.margin = int(cfg.get_path("ui.orb_margin", 28))
        self.position = cfg.get_path("ui.orb_position", "bottom-right")
        self.always_on_top = bool(cfg.get_path("ui.always_on_top", True))
        theme = cfg.get_path("ui.theme", {}) or {}
        self.colours = {
            "idle": theme.get("idle", "#2b3a4a"),
            "listening": theme.get("listening", "#00d4ff"),
            "thinking": theme.get("thinking", "#f5a623"),
            "speaking": theme.get("speaking", "#3ddc84"),
            "blocked": theme.get("blocked", "#ff4d4f"),
            "muted": "#4a4a4a",
        }
        self._q: queue.Queue[tuple[str, object]] = queue.Queue()
        self._state = "idle"
        self._level = 0.0
        self._phase = 0.0
        self._root: tk.Tk | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -------------------------------------------------------------- public API
    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="orb", daemon=True)
        self._thread.start()

    def set_state(self, state: str) -> None:
        if state in STATES:
            self._q.put(("state", state))

    def set_level(self, level: float) -> None:
        """0..1 — mic energy while listening, drives the ring thickness."""
        self._q.put(("level", max(0.0, min(1.0, float(level)))))

    def flash(self, state: str = "blocked", seconds: float = 1.2) -> None:
        self._q.put(("flash", (state, seconds)))

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ render
    def _geometry(self, root: tk.Tk) -> str:
        w = h = self.size + 24
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        taskbar = 48
        if self.position == "bottom-right":
            x, y = sw - w - self.margin, sh - h - self.margin - taskbar
        elif self.position == "bottom-left":
            x, y = self.margin, sh - h - self.margin - taskbar
        elif self.position == "top-right":
            x, y = sw - w - self.margin, self.margin
        else:
            x, y = self.margin, self.margin
        return f"{w}x{h}+{x}+{y}"

    def _run(self) -> None:
        root = tk.Tk()
        self._root = root
        root.overrideredirect(True)
        root.attributes("-topmost", self.always_on_top)
        root.config(bg=CHROMA)
        try:
            root.attributes("-transparentcolor", CHROMA)
        except tk.TclError:
            pass  # non-Windows: orb shows on a solid square, still works
        root.geometry(self._geometry(root))

        canvas = tk.Canvas(
            root, width=self.size + 24, height=self.size + 24,
            bg=CHROMA, highlightthickness=0, bd=0,
        )
        canvas.pack()

        # drag to reposition
        drag = {"x": 0, "y": 0}
        canvas.bind("<Button-1>", lambda e: drag.update(x=e.x, y=e.y))
        canvas.bind(
            "<B1-Motion>",
            lambda e: root.geometry(f"+{root.winfo_x()+e.x-drag['x']}+{root.winfo_y()+e.y-drag['y']}"),
        )

        def tick() -> None:
            if self._stop.is_set():
                root.destroy()
                return
            while not self._q.empty():
                kind, value = self._q.get_nowait()
                if kind == "state":
                    self._state = str(value)
                elif kind == "level":
                    self._level = float(value)  # type: ignore[arg-type]
                elif kind == "flash":
                    state, seconds = value  # type: ignore[misc]
                    previous = self._state
                    self._state = state
                    root.after(int(float(seconds) * 1000), lambda: setattr(self, "_state", previous))

            self._phase += 0.13
            canvas.delete("all")
            cx = cy = (self.size + 24) / 2
            colour = self.colours.get(self._state, self.colours["idle"])

            pulse = {
                "idle": 0.03 * math.sin(self._phase * 0.5),
                "listening": 0.10 * self._level + 0.04 * math.sin(self._phase * 1.6),
                "thinking": 0.09 * math.sin(self._phase * 2.2),
                "speaking": 0.11 * math.sin(self._phase * 3.4),
                "blocked": 0.0,
                "muted": 0.0,
            }.get(self._state, 0.0)

            base = self.size / 2
            # soft halo
            for i, alpha in enumerate((0.30, 0.55, 1.0)):
                r = base * (1 + pulse) * (1.18 - i * 0.09)
                canvas.create_oval(
                    cx - r, cy - r, cx + r, cy + r,
                    outline=colour, width=max(1, int(3 * alpha)),
                )
            # core
            r = base * 0.56 * (1 + pulse * 0.6)
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=colour, outline="")

            if self._state == "muted":
                canvas.create_line(
                    cx - base * 0.5, cy - base * 0.5, cx + base * 0.5, cy + base * 0.5,
                    fill="#ff6b6b", width=3,
                )
            root.after(40, tick)

        tick()
        root.mainloop()


class TranscriptWindow:
    """Spec B15 — long content goes here instead of being read out."""

    def __init__(self, cfg) -> None:
        self.enabled = bool(cfg.get_path("ui.transcript_window", True))
        self._q: queue.Queue[tuple[str, str]] = queue.Queue()
        self._thread: threading.Thread | None = None

    def show(self, title: str, body: str) -> None:
        if not self.enabled:
            return
        self._q.put((title, body))
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self) -> None:
        try:
            title, body = self._q.get(timeout=1)
        except queue.Empty:
            return
        root = tk.Tk()
        root.title(f"Jarvis — {title}")
        root.geometry("760x560")
        root.configure(bg="#14181d")
        frame = tk.Frame(root, bg="#14181d")
        frame.pack(fill="both", expand=True, padx=14, pady=14)
        scroll = tk.Scrollbar(frame)
        scroll.pack(side="right", fill="y")
        text = tk.Text(
            frame, wrap="word", bg="#14181d", fg="#e8edf2",
            insertbackground="#e8edf2", font=("Segoe UI", 11),
            relief="flat", yscrollcommand=scroll.set, padx=10, pady=10,
        )
        text.pack(fill="both", expand=True)
        scroll.config(command=text.yview)
        text.insert("1.0", body)
        text.config(state="disabled")
        root.mainloop()
