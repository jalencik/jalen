"""
The floating orb (spec C21).

tkinter, not PySide6 — deliberately. Qt would look nicer but costs ~90 MB
resident, and on a machine sitting at 86% memory that is a real tax for a
decoration. tkinter ships with Python and the whole overlay costs about 18 MB.
If you later free up RAM and want the prettier version, this is the one file
to replace.

Windows transparency trick: a fully transparent colour key. Anything painted in
CHROMA becomes see-through, so the orb looks like it's floating on the desktop.

STATE VISIBILITY (this revision). The first version told states apart by
colour alone: six near-identically-shaped pulsing rings in different hues.
That fails at a glance and fails outright for colour-blind users. Every state
below now has its own SHAPE and MOTION, colour is just the fastest cue on top
of that:

    idle       a small, mostly still dot. No ring. The point is that it
               reads as "off" — this is the "silent/dark when idle" state.
    listening  two rings ping outward on a loop (sonar), core swells with
               live mic energy. BLUE. The only state that reacts to your
               voice in real time.
    thinking   a single arc ROTATES around a thin static ring — "processing",
               not "pulsing". Doubles as "executing": this is the state the
               app sets while a tool call or brain turn is in flight. YELLOW.
    speaking   eight spokes tick in and out like a level meter — the only
               state that looks like it's making sound. GREEN.
    blocked    a hard on/off BLINK (not a smooth pulse) plus an exclamation
               mark drawn on the core, so the meaning survives even with
               colour vision removed entirely. RED.
    muted      a small dim dot with a diagonal slash through it. Static.

Idle and muted redraw at ~7fps instead of ~25fps — nothing is moving there
worth spending CPU on every 40ms, and this is a background decoration on a
machine that is already short on headroom.
"""
from __future__ import annotations

import ctypes
import math
import platform
import queue
import threading
import time
import tkinter as tk

IS_WINDOWS = platform.system() == "Windows"

CHROMA = "#010203"  # a colour nothing else will use

STATES = ("idle", "listening", "thinking", "speaking", "blocked", "muted")

# How long a heard transcript stays on screen before it auto-hides (ui.show_transcript).
TRANSCRIPT_TTL_S = 8.0
# This is a glance-sized caption, not a place to read a paragraph — long
# content still goes to TranscriptWindow, unchanged, below.
TRANSCRIPT_MAX_CHARS = 64
LABEL_H = 20  # extra window height reserved for the caption row

# The canvas is this many times the orb diameter. The expanding wave
# rings reach 1.55x the base radius and anything past the canvas edge is
# clipped square, so headroom is what stops the waves being sliced off.
WAVE_HEADROOM = 1.8

# Resize limits. Below the minimum the per-state shapes stop being
# distinguishable, which is the whole point of them; above the maximum it
# has stopped being an overlay.
MIN_ORB = 120
MAX_ORB = 900

# States whose animation is worth a smooth ~25fps redraw. Everything else
# (idle, muted) redraws at ~7fps — see module docstring.
_ACTIVE_STATES = ("listening", "thinking", "speaking", "blocked")

# ---------------------------------------------------------------------------
# Win32 click-through (ui.click_through_when_idle)
# ---------------------------------------------------------------------------
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020


def set_click_through(hwnd: int, enable: bool) -> bool:
    """
    Add/remove WS_EX_TRANSPARENT on the orb's own window so an idle orb stops
    eating clicks meant for whatever is underneath it — the window becomes
    click-through but keeps painting exactly as before.

    WS_EX_LAYERED is required for WS_EX_TRANSPARENT to do anything and is
    OR'd in alongside it, never cleared: Tk already relies on that same bit
    for the -transparentcolor chroma-key, so clearing it here would also
    break the see-through background.

    Returns True if the style call actually went through, False if this
    isn't Windows or user32 rejected it — callers use that to decide whether
    to keep believing click-through is active, rather than assuming success.
    """
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if style == 0:
            return False  # GetWindowLongW failing returns 0; a real style is never 0 here
        if enable:
            new_style = style | WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            new_style = (style | WS_EX_LAYERED) & ~WS_EX_TRANSPARENT
        if new_style != style:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        return True
    except OSError:
        return False


def _mix(colour: str, target: str, t: float) -> str:
    """
    Blend two #rrggbb colours. Used to dim a state's own colour toward black
    for backdrop rings / the "off" phase of a blink, since plain tkinter
    Canvas items have no real alpha channel to fade with.
    """
    try:
        t = max(0.0, min(1.0, t))
        c1 = tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))
        c2 = tuple(int(target[i:i + 2], 16) for i in (1, 3, 5))
        mixed = tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))
        return "#%02x%02x%02x" % mixed
    except (ValueError, IndexError):
        return colour  # a malformed theme colour degrades to solid, not a crash


class Orb:
    def __init__(self, cfg) -> None:
        self.enabled = bool(cfg.get_path("ui.orb", True))
        self.size = int(cfg.get_path("ui.orb_size", 84))
        self.margin = int(cfg.get_path("ui.orb_margin", 28))
        self.position = cfg.get_path("ui.orb_position", "bottom-right")
        self.always_on_top = bool(cfg.get_path("ui.always_on_top", True))
        self.show_transcript = bool(cfg.get_path("ui.show_transcript", True))
        self.click_through_when_idle = bool(cfg.get_path("ui.click_through_when_idle", True))
        theme = cfg.get_path("ui.theme", {}) or {}
        self.colours = {
            "idle": theme.get("idle", "#161c24"),
            "listening": theme.get("listening", "#2f8fff"),
            "thinking": theme.get("thinking", "#ffd60a"),
            "speaking": theme.get("speaking", "#3ddc84"),
            "blocked": theme.get("blocked", "#ff4d4f"),
            "muted": "#4a4a4a",
        }
        self._q: queue.Queue[tuple[str, object]] = queue.Queue()
        self._state = "idle"
        self._level = 0.0
        self._heard = ""
        self._heard_at = 0.0
        self._click_through_applied: bool | None = None
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
        """0..1 — mic energy while listening, drives the ring/core size."""
        self._q.put(("level", max(0.0, min(1.0, float(level)))))

    def set_transcript(self, text: str) -> None:
        """
        What speech-to-text actually heard, for the caption line under the
        orb (ui.show_transcript). Without this the user cannot tell a
        misheard command from a broken one without reading a terminal.
        Empty/whitespace text is ignored rather than shown as a blank caption.
        """
        text = (text or "").strip()
        if text:
            self._q.put(("heard", text))

    def flash(self, state: str = "blocked", seconds: float = 1.2) -> None:
        self._q.put(("flash", (state, seconds)))

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ render
    def _geometry(self, root: tk.Tk, w: int, h: int) -> str:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        taskbar = 48
        if self.position == "center":
            # Centred horizontally, and slightly ABOVE centre vertically —
            # true centre puts it behind whatever he is reading, and the
            # optical centre of a screen sits a little high anyway.
            x, y = (sw - w) // 2, int((sh - h) * 0.42)
        elif self.position == "bottom-right":
            x, y = sw - w - self.margin, sh - h - self.margin - taskbar
        elif self.position == "bottom-left":
            x, y = self.margin, sh - h - self.margin - taskbar
        elif self.position == "top-right":
            x, y = sw - w - self.margin, self.margin
        else:
            x, y = self.margin, self.margin
        return f"{w}x{h}+{x}+{y}"

    def resize_by(self, delta: int) -> None:
        """Grow or shrink the orb, clamped to something still usable."""
        self._q.put(("resize", int(delta)))

    @staticmethod
    def _clamp_size(root: tk.Tk, size: int) -> int:
        """
        Keep the orb inside the screen it is being drawn on.

        A fixed MAX_ORB is not enough, and the difference is visible: 600 on
        this 1536x864 display produced a 1100-pixel-tall window positioned at
        y = -99, so the top of the orb was simply off the screen. The window
        is WAVE_HEADROOM times the orb, and it has to fit the SHORT side of
        whatever monitor it lands on — which is not knowable from a constant.

        0.85 rather than 1.0 so it stays an overlay with desktop visible
        around it, instead of a disc that reaches the edges.
        """
        try:
            usable = min(root.winfo_screenwidth(), root.winfo_screenheight())
        except tk.TclError:
            usable = 864
        ceiling = int(usable * 0.85 / WAVE_HEADROOM)
        return max(MIN_ORB, min(MAX_ORB, ceiling, int(size)))

    def _draw_waves(self, canvas: tk.Canvas, cx: float, cy: float,
                    base: float, now: float, colour: str) -> None:
        """
        Rings expanding outward and fading, behind the orb.

        This is what he meant by "waves", and by the orb looking like
        something is happening rather than a dot in a corner. Drawn FIRST so
        everything else sits on top of it.

        Fading is faked by mixing toward the background, because a Tk canvas
        item has no alpha channel — the same trick _mix() already exists for.
        Rings are staggered by a fixed phase offset so they read as a pulse
        travelling outward instead of one ring blinking.

        Idle is deliberately quiet: one slow, barely-there ring. He asked for
        black when stopped, and a resting assistant that keeps pulsing at you
        is one you end up hiding.
        """
        rings, period, reach = (1, 4.0, 1.15) if self._state in ("idle", "muted") else (3, 2.0, 1.55)
        for index in range(rings):
            phase = ((now / period) + index / rings) % 1.0
            radius = base * (0.42 + phase * (reach - 0.42))
            # Brightest as it leaves the core, gone by the time it reaches
            # the edge — otherwise the rings pile up at the boundary.
            fade = 1.0 - phase
            ring = _mix(CHROMA, colour, fade * (0.30 if self._state in ("idle", "muted") else 0.75))
            width = max(1, int(3 * fade) + 1)
            canvas.create_oval(
                cx - radius, cy - radius, cx + radius, cy + radius,
                outline=ring, width=width,
            )

    def _draw_orb(self, canvas: tk.Canvas, cx: float, cy: float, base: float, now: float) -> None:
        state = self._state
        colour = self.colours.get(state, self.colours["idle"])
        dim = _mix(colour, "#000000", 0.6)
        self._draw_waves(canvas, cx, cy, base, now, colour)

        if state == "idle":
            # The quietest thing on screen: no ring, a small core, a breathe
            # so slow (6s) it reads as "alive", not "pulsing".
            r = base * 0.30 * (1 + 0.05 * math.sin(now * (2 * math.pi / 6.0)))
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=colour, outline="")
            return

        if state == "muted":
            r = base * 0.34
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=colour, outline="")
            canvas.create_line(
                cx - base * 0.5, cy - base * 0.5, cx + base * 0.5, cy + base * 0.5,
                fill="#ff6b6b", width=3,
            )
            return

        if state == "listening":
            # Two rings ping outward on a loop (sonar), fading as they grow.
            # Core swells with live mic energy — the only state that reacts
            # to your voice in real time.
            for i in range(2):
                frac = (now * 0.9 + i * 0.5) % 1.0
                r = base * (0.55 + 0.85 * frac)
                w = max(1, int(5 * (1 - frac)))
                ring_colour = _mix(colour, "#000000", frac * 0.6)
                canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=ring_colour, width=w)
            r = base * (0.42 + 0.22 * self._level)
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=colour, outline="")
            return

        if state == "thinking":
            # A rotating arc around a thin static ring — "processing", not
            # "pulsing". This is also the state set while a tool call or
            # brain turn is executing (see app.py), i.e. "YELLOW when
            # executing" from the spec.
            r = base * 0.62
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=dim, width=2)
            angle = (now * 220) % 360
            canvas.create_arc(
                cx - r, cy - r, cx + r, cy + r, start=angle, extent=110,
                style=tk.ARC, outline=colour, width=6,
            )
            core = base * 0.40
            canvas.create_oval(cx - core, cy - core, cx + core, cy + core, fill=colour, outline="")
            return

        if state == "speaking":
            # Eight spokes tick in and out like a level meter — the only
            # state that looks like it's making sound.
            n = 8
            for i in range(n):
                ang = (2 * math.pi * i / n) + now * 0.6
                amp = 0.5 + 0.5 * math.sin(now * 7.0 + i * 1.3)
                inner = base * 0.55
                outer = inner + base * 0.35 * amp
                x1, y1 = cx + inner * math.cos(ang), cy + inner * math.sin(ang)
                x2, y2 = cx + outer * math.cos(ang), cy + outer * math.sin(ang)
                canvas.create_line(x1, y1, x2, y2, fill=colour, width=4, capstyle=tk.ROUND)
            r = base * 0.46
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=colour, outline="")
            return

        if state == "blocked":
            # A hard on/off BLINK, not a smooth pulse — deliberately the
            # most jarring animation on the orb — plus an exclamation mark
            # drawn on the core so the meaning survives even with colour
            # vision removed entirely.
            lit = int(now * 2.5) % 2 == 0
            r = base * 0.68
            ring_colour = colour if lit else dim
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=ring_colour, width=7)
            if lit:
                core = base * 0.5
                canvas.create_oval(cx - core, cy - core, cx + core, cy + core, fill=colour, outline="")
                canvas.create_line(
                    cx, cy - base * 0.22, cx, cy + base * 0.04,
                    fill="#1a1a1a", width=4, capstyle=tk.ROUND,
                )
                canvas.create_oval(
                    cx - 2.5, cy + base * 0.16, cx + 2.5, cy + base * 0.21,
                    fill="#1a1a1a", outline="",
                )
            return

        # Unknown state (shouldn't happen — set_state() filters against
        # STATES) falls back to a plain idle-style dot instead of drawing
        # nothing, so a bad value is visible rather than silently blank.
        r = base * 0.30
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=colour, outline="")

    def _draw_caption(self, canvas: tk.Canvas, canvas_w: int, orb_h: int, now: float) -> None:
        if not self.show_transcript or not self._heard:
            return
        if now - self._heard_at > TRANSCRIPT_TTL_S:
            return
        text = self._heard
        if len(text) > TRANSCRIPT_MAX_CHARS:
            text = text[: TRANSCRIPT_MAX_CHARS - 1].rstrip() + "…"
        y0, y1 = orb_h + 1, orb_h + LABEL_H - 1
        canvas.create_rectangle(3, y0, canvas_w - 3, y1, fill="#12161c", outline="")
        canvas.create_text(
            canvas_w / 2, (y0 + y1) / 2, text=text, fill="#d7dee6", font=("Segoe UI", 8),
        )

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

        # WAVE_HEADROOM, not the old flat +24. The expanding rings reach
        # 1.55x the base radius, and anything beyond the canvas is simply
        # clipped — the old margin was sized for a static dot, so rings would
        # have been sliced off square at the window edge.
        label_h = LABEL_H if self.show_transcript else 0

        def canvas_size() -> tuple[int, int]:
            wh = int(self.size * WAVE_HEADROOM)
            return wh, wh + label_h

        # The configured size gets the same treatment as a resize: a
        # ui.orb_size larger than the screen would otherwise open off-screen
        # on the very first frame.
        self.size = self._clamp_size(root, self.size)
        canvas_w, canvas_h = canvas_size()
        root.geometry(self._geometry(root, canvas_w, canvas_h))

        canvas = tk.Canvas(
            root, width=canvas_w, height=canvas_h,
            bg=CHROMA, highlightthickness=0, bd=0,
        )
        canvas.pack()

        def apply_size() -> None:
            """Resize the window and canvas around the new orb size."""
            nonlocal canvas_w, canvas_h
            canvas_w, canvas_h = canvas_size()
            canvas.config(width=canvas_w, height=canvas_h)
            root.geometry(self._geometry(root, canvas_w, canvas_h))

        # drag to reposition
        drag = {"x": 0, "y": 0}
        canvas.bind("<Button-1>", lambda e: drag.update(x=e.x, y=e.y))
        canvas.bind(
            "<B1-Motion>",
            lambda e: root.geometry(f"+{root.winfo_x()+e.x-drag['x']}+{root.winfo_y()+e.y-drag['y']}"),
        )

        # Scroll over the orb to resize it.
        #
        # He asked to size it with his hands, like the photo — that is camera
        # hand-tracking, a webcam and a whole extra dependency, and it is not
        # in this change. This is the honest thing that works today: point at
        # the orb and scroll. Recentring after every resize is deliberate;
        # growing a centred orb from its top-left corner walks it down the
        # screen.
        def on_wheel(event) -> None:
            self.resize_by(24 if event.delta > 0 else -24)

        canvas.bind("<MouseWheel>", on_wheel)

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
                elif kind == "heard":
                    self._heard = str(value)
                    self._heard_at = time.monotonic()
                elif kind == "flash":
                    state, seconds = value  # type: ignore[misc]
                    previous = self._state
                    self._state = state
                    root.after(int(float(seconds) * 1000), lambda: setattr(self, "_state", previous))
                elif kind == "resize":
                    self.size = self._clamp_size(root, self.size + int(value))  # type: ignore[arg-type]
                    apply_size()

            now = time.monotonic()
            canvas.delete("all")
            orb_area = canvas_h - (LABEL_H if self.show_transcript else 0)
            cx, cy = canvas_w / 2, orb_area / 2
            base = self.size / 2
            self._draw_orb(canvas, cx, cy, base, now)
            self._draw_caption(canvas, canvas_w, orb_area, now)

            if self.click_through_when_idle:
                want = self._state == "idle"
                if want != self._click_through_applied:
                    if set_click_through(root.winfo_id(), want):
                        self._click_through_applied = want

            interval = 40 if self._state in _ACTIVE_STATES else 150
            root.after(interval, tick)

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
        root.title(f"Jalen — {title}")
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
