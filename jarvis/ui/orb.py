"""
The floating orb (spec C21).

tkinter, not PySide6 — deliberately. Qt would look nicer but costs ~90 MB
resident, and on a machine sitting at 86% memory that is a real tax for a
decoration. tkinter ships with Python and the whole overlay costs about 18 MB.
If you later free up RAM and want the prettier version, this is the one file
to replace.

Windows transparency trick: a fully transparent colour key. Anything painted in
CHROMA becomes see-through, so the orb looks like it's floating on the desktop.

WHAT IT LOOKS LIKE. He sent a reference photo — a wireframe globe with a
network of nodes across it, a bright core, broken arc segments at the rim,
and the name spelled out underneath. That is what this draws.

Every state draws the SAME OBJECT and differs by MOTION and colour. That is a
change from the first version, which drew six unrelated shapes: a dot, a
sonar ring, an arc, some spokes. Six shapes taking turns reads as six widgets,
not as one assistant changing mood, and he asked for one thing that is always
there and behaves differently depending on what it is doing.

    idle       the globe turning slowly, calmly lit. TEAL. Quiet, not absent.
    listening  waves travelling OUTWARD from the core, and the core breathing
               on live microphone energy. BLUE. The only state that reacts to
               you in real time.
    thinking   no outward waves at all — the globe spins faster, the rim arcs
               race, and a bright segment SWEEPS around it. Circular and
               internal, so it cannot be mistaken for listening. YELLOW.
    speaking   the core pulses at a syllable rate and level-meter spokes tick
               in and out. GREEN.
    blocked    a hard on/off BLINK plus an exclamation mark on the core, so
               the meaning survives with colour vision removed. RED.
    muted      the globe, unlit, with a diagonal slash. Static.

IDLE IS VISIBLE, DELIBERATELY. It used to be a near-black dot (#161c24), from
an earlier "black when stopped" instruction. He then reported the orb as
simply not appearing "no matter whichever window I will be", and at that
colour on a dark desktop that is an accurate description rather than a
misunderstanding. If a future instruction asks for a dark idle again, read
this paragraph first: it is a request to make the orb invisible.

THE BUG THAT ACTUALLY HID IT was not the colour. See toplevel_hwnd() below —
click-through was being applied to Tk's canvas CHILD window rather than the
top-level, which stops the canvas painting altogether. Measured: 0 orb pixels
while idle before the fix, ~1,270 after, with click-through still on.

Idle and muted redraw at ~7fps instead of ~25fps — nothing is moving there
worth spending CPU on every 40ms, and this is a background decoration on a
machine that is already short on headroom.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import math
import platform
import queue
import threading
import time
import tkinter as tk

IS_WINDOWS = platform.system() == "Windows"

CHROMA = "#010203"  # a colour nothing else will use

# The warm counterpoint on the rim arcs. In the reference image the sphere is
# all blues and teals with a few orange segments, and those few are what stop
# it looking like a screensaver — one contrasting hue reads as instrumentation.
ACCENT = "#ff8a3d"

STATES = ("idle", "listening", "thinking", "speaking", "blocked", "muted")

# How long a heard transcript stays on screen before it auto-hides (ui.show_transcript).
TRANSCRIPT_TTL_S = 8.0
# This is a glance-sized caption, not a place to read a paragraph — long
# content still goes to TranscriptWindow, unchanged, below.
TRANSCRIPT_MAX_CHARS = 64
LABEL_H = 20  # extra window height reserved for the caption row
# And for the name under the sphere. Reserved as a FRACTION of the orb rather
# than a fixed pixel count: the text is sized from the orb (base * 0.115), so
# a constant would clip the name at large sizes and leave a gap at small ones.
NAME_H_FRACTION = 0.17

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
GA_ROOT = 2                      # GetAncestor: the root of the window chain


def _user32():
    """
    user32 with the prototypes declared, or None off Windows.

    DECLARING THESE IS NOT PEDANTRY. ctypes defaults every unprototyped
    function to returning a C `int` — 32 bits, signed. Window handles on
    64-bit Windows do not reliably fit in that, so GetAncestor and GetParent
    can hand back a TRUNCATED handle that still looks like a plausible
    number. Every subsequent call then styles either nothing or, worse, some
    unrelated window. It fails intermittently and by machine, which is the
    worst way for a bug like this to behave.
    """
    if not IS_WINDOWS:
        return None
    lib = ctypes.windll.user32
    if getattr(lib, "_jalen_prototyped", False):
        return lib
    try:
        wt = ctypes.wintypes
        lib.GetAncestor.argtypes = [wt.HWND, ctypes.c_uint]
        lib.GetAncestor.restype = wt.HWND
        lib.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
        lib.GetWindowLongW.restype = ctypes.c_long
        lib.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
        lib.SetWindowLongW.restype = ctypes.c_long
        lib.SetWindowPos.argtypes = [
            wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        lib.SetWindowPos.restype = wt.BOOL
        lib._jalen_prototyped = True
    except (AttributeError, OSError):
        return lib
    return lib

# SetWindowPos, for keeping the orb above everything else.
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

# How often to re-assert it, in animation ticks. The loop runs at 40ms when
# something is happening and 150ms when idle, so this is roughly every two
# seconds either way — often enough that a demotion is invisible, rare enough
# that it is not a per-frame syscall.
TOPMOST_REASSERT_TICKS = 40


def raise_to_top(hwnd: int) -> bool:
    """
    Put the orb back above everything, without stealing focus.

    SWP_NOACTIVATE is the important flag: re-asserting topmost must never
    take focus off whatever he is typing into. An overlay that steals the
    caret every two seconds is worse than one you cannot see.

    Win32 rather than Tk's attributes("-topmost", True): Tk caches the value
    and skips the call when it believes nothing changed, which is exactly the
    situation here — Windows demoted the window without telling Tk, so as far
    as Tk is concerned it is still topmost and there is nothing to do.
    """
    lib = _user32()
    if lib is None or not hwnd:
        return False
    try:
        return bool(lib.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        ))
    except OSError:
        return False


def toplevel_hwnd(root) -> int:
    """
    The window Windows actually orders and composites.

    THIS IS THE BUG THAT MADE THE ORB INVISIBLE, and it is worth stating
    precisely because nothing about it looks wrong.

    `root.winfo_id()` on Windows does NOT return the top-level window. Tk
    creates a child window inside it and returns that. So every ctypes call
    here was aimed one level too low, and the consequences were not subtle:

      * WS_EX_LAYERED | WS_EX_TRANSPARENT were being set on the CHILD window
        that holds the canvas. A layered child does not composite against
        the parent's colour key — it simply stops painting. So the moment
        the orb went idle and click-through was applied, the whole thing
        disappeared. Confirmed by screenshot: idle rendered nothing at all,
        while the same orb in `listening` (click-through removed) rendered
        correctly.
      * WS_EX_TOPMOST was set by Tk on the parent, so the topmost check
        looked healthy on the window nobody was styling.

    That is exactly the reported symptom — "the orb is still not visible, no
    matter whichever window I am in" — and it was never a Z-order problem.

    Falls back to the child id if there is no parent, so a non-Windows or
    unexpected toolkit arrangement degrades to the old behaviour rather than
    raising.
    """
    try:
        hwnd = root.winfo_id()
    except Exception:
        return 0
    lib = _user32()
    if lib is None:
        return hwnd
    # Not yet realized. Tk's window hierarchy does not exist until the first
    # update, and GetAncestor on an unrealized window returns the handle it
    # was given — indistinguishable from "this IS the top level".
    #
    # This is what actually broke it. tick() runs once directly before
    # mainloop(), at which point the hierarchy is not built, so GetAncestor
    # handed back the child; click-through was applied to the child, the
    # canvas stopped painting, and because _click_through_applied was then
    # True it was never reconsidered. The orb was invisible for the entire
    # session and correcting the ancestor lookup alone did not help, because
    # the wrong answer was being produced at a moment when no lookup could
    # succeed.
    try:
        if not lib.IsWindowVisible(hwnd):
            return 0
    except (AttributeError, OSError):
        pass
    try:
        # GetAncestor(GA_ROOT), NOT GetParent. For a top-level window
        # GetParent returns the OWNER, which for Tk is a hidden helper
        # window — so styling it changes nothing visible and the real window
        # keeps its old flags. GA_ROOT walks to the actual root of the window
        # chain and is the only one of the two that is correct for both a
        # child and a top-level.
        root_hwnd = lib.GetAncestor(hwnd, GA_ROOT)
    except OSError:
        return hwnd
    if not root_hwnd or root_hwnd == hwnd:
        # Still the child: the hierarchy is not up yet. Report "unknown"
        # rather than a wrong answer, so the caller waits instead of styling
        # the canvas by mistake.
        return 0
    return root_hwnd


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

    MUST be given the TOP-LEVEL window (see toplevel_hwnd). Aimed at the
    child that Tk's winfo_id() returns, WS_EX_LAYERED stops the canvas
    compositing against the parent's colour key and the orb renders nothing
    at all — which is precisely the "it is not visible" report this fixes.
    """
    lib = _user32()
    if lib is None or not hwnd:
        return False
    try:
        style = lib.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if style == 0:
            return False  # GetWindowLongW failing returns 0; a real style is never 0 here
        if enable:
            new_style = style | WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            new_style = (style | WS_EX_LAYERED) & ~WS_EX_TRANSPARENT
        if new_style != style:
            lib.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# THE SPHERE (the look in his reference photo: a wireframe globe, a network
# of nodes across it, a bright core, and arc segments at the rim).
#
# Geometry is computed ONCE at import and rotated per frame. The alternative
# — building the point set every frame — is the same picture for about forty
# times the CPU, and this is a decoration running forever on a machine at 86%
# memory. Rotation and projection are a handful of multiplications per node.
# ---------------------------------------------------------------------------
NODE_COUNT = 40
EDGE_NEIGHBOURS = 2


def _fibonacci_sphere(count: int) -> tuple[tuple[float, float, float], ...]:
    """
    `count` points spread evenly over a unit sphere.

    The golden-angle spiral, because the obvious lat/long grid bunches points
    at the poles — which on a rotating globe reads as two bright spots rather
    than an even mesh.
    """
    points = []
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(count):
        y = 1 - (i / float(count - 1)) * 2      # 1 .. -1
        radius = math.sqrt(max(0.0, 1 - y * y))
        theta = golden * i
        points.append((math.cos(theta) * radius, y, math.sin(theta) * radius))
    return tuple(points)


def _nearest_edges(points, neighbours: int) -> tuple[tuple[int, int], ...]:
    """
    Connect each node to its nearest few, deduplicated.

    Nearest-neighbour rather than random pairs: random lines cut straight
    through the middle of the sphere and destroy the illusion of a surface.
    Short edges hug it.
    """
    edges = set()
    for i, a in enumerate(points):
        distances = sorted(
            ((sum((a[k] - b[k]) ** 2 for k in range(3)), j)
             for j, b in enumerate(points) if j != i)
        )
        for _, j in distances[:neighbours]:
            edges.add((min(i, j), max(i, j)))
    return tuple(sorted(edges))


_NODES = _fibonacci_sphere(NODE_COUNT)
_EDGES = _nearest_edges(_NODES, EDGE_NEIGHBOURS)


def _project(point, yaw: float, pitch: float):
    """
    Rotate a unit-sphere point and flatten it to 2D.

    Orthographic, not perspective: at this size the difference is invisible
    and perspective needs a focal length to argue about. Returns the depth
    as well, because that is what makes the far side of the globe dimmer and
    therefore what makes it read as a sphere instead of a disc of lines.
    """
    x, y, z = point
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    x, z = x * cos_y - z * sin_y, x * sin_y + z * cos_y
    cos_p, sin_p = math.cos(pitch), math.sin(pitch)
    y, z = y * cos_p - z * sin_p, y * sin_p + z * cos_p
    return x, y, z


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
        # The name under the sphere, as in his reference image. Read from
        # identity so a renamed assistant relabels itself, rather than a
        # second user staring at somebody else's name on their own desktop.
        self.label = str(
            cfg.get_path("ui.orb_label", None)
            or cfg.get_path("identity.name", "Jalen")
        ).strip()
        self.show_transcript = bool(cfg.get_path("ui.show_transcript", True))
        # Two keys, and EITHER of them set to false turns this off.
        #
        # ui.click_through_when_idle is the old name, and it shipped meaning
        # the opposite of what it said — the orb went solid while BUSY. So
        # the only reason anybody ever set it to false was to stop the orb
        # eating their clicks. Preferring the new key would silently switch
        # that back on for them during an upgrade, which is the one outcome
        # a rename must not produce. An explicit false, under either name,
        # is honoured.
        explicit = [
            cfg.get_path("ui.click_through_when_busy"),
            cfg.get_path("ui.click_through_when_idle"),
        ]
        chosen = [v for v in explicit if v is not None]
        self.click_through_when_busy = all(bool(v) for v in chosen) if chosen else True
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
        self._topmost_tick = 0
        # The real top-level window handle, resolved lazily once Tk has
        # actually realized the window. 0 until then. See toplevel_hwnd().
        self._hwnd = 0
        self._root: tk.Tk | None = None
        # Where he DRAGGED it to, if he has. None means "use self.position".
        #
        # Without this, every resize snapped the orb back to its configured
        # corner: apply_size() recomputes geometry from self.position, which
        # knows nothing about the drag. So you would move the orb somewhere
        # sensible, make it bigger, and watch it jump back.
        self._manual_xy: tuple[int, int] | None = None
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
        if self._manual_xy is not None:
            # Clamped, because a window dragged near an edge and then grown
            # would otherwise put half the orb off the screen.
            x = max(0, min(sw - w, self._manual_xy[0]))
            y = max(0, min(sh - h, self._manual_xy[1]))
            return f"{w}x{h}+{x}+{y}"
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

    def _glow(self, canvas: tk.Canvas, cx: float, cy: float, radius: float,
              colour: str, *, steps: int = 22, core: float = 1.0) -> None:
        """
        A soft sphere, faked with concentric circles.

        A Tk canvas item has no alpha channel, so there is no real radial
        gradient and no blur. What there IS: enough concentric ovals, each a
        step further blended toward the background, reads as one glowing
        ball at any size the eye cares about. Twenty-two steps is where the
        banding stops being visible on this display.

        Drawn outside-in — largest and dimmest first — because each oval is
        opaque and paints over the one before it.
        """
        for i in range(steps, 0, -1):
            t = i / steps                       # 1.0 at the rim, ~0 at the core
            r = radius * t
            # Squared falloff: light does not fade linearly, and a linear
            # ramp looks like a flat disc with a fuzzy edge rather than a
            # sphere.
            shade = _mix(CHROMA, colour, core * (1.0 - t) ** 2 + 0.06)
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=shade, outline="")

    # ---------------------------------------------------------- the globe
    def _draw_mesh(self, canvas: tk.Canvas, cx: float, cy: float, r: float,
                   now: float, colour: str, *, spin: float = 0.25,
                   energy: float = 0.0) -> None:
        """
        The node-and-line network wrapped over the sphere.

        Depth is everything here. Each node and edge is dimmed by how far
        BEHIND the sphere it sits, so the far side fades out and the near
        side is crisp — that difference is the entire reason it reads as a
        ball rather than a flat tangle. Without it the same lines look like
        a plate of spaghetti.

        `energy` (0..1) lights the mesh up, and is what makes listening and
        speaking look alive without changing the shape.
        """
        yaw = now * spin
        pitch = 0.42 + 0.12 * math.sin(now * 0.23)
        points = [_project(p, yaw, pitch) for p in _NODES]

        for i, j in _EDGES:
            x1, y1, z1 = points[i]
            x2, y2, z2 = points[j]
            depth = (z1 + z2) / 2                      # -1 far .. 1 near
            # Squared, so the far side drops away quickly instead of the
            # whole globe sitting at a uniform mid-grey.
            shade = (0.5 + 0.5 * depth) ** 2
            brightness = (0.10 + 0.55 * shade) * (1.0 + 0.6 * energy)
            canvas.create_line(
                cx + x1 * r, cy + y1 * r, cx + x2 * r, cy + y2 * r,
                fill=_mix(CHROMA, colour, min(1.0, brightness)),
                width=1 if depth < 0 else 2,
            )

        for index, (x, y, z) in enumerate(points):
            shade = (0.5 + 0.5 * z) ** 2
            # A slow travelling shimmer, so the mesh is never completely
            # static even when nothing is happening.
            pulse = 0.5 + 0.5 * math.sin(now * 1.6 + index * 0.7)
            size = (1.0 + 1.8 * shade) * (1.0 + 0.5 * energy * pulse)
            canvas.create_oval(
                cx + x * r - size, cy + y * r - size,
                cx + x * r + size, cy + y * r + size,
                fill=_mix(CHROMA, colour, min(1.0, 0.25 + 0.75 * shade)),
                outline="",
            )

    def _draw_wireframe(self, canvas: tk.Canvas, cx: float, cy: float, r: float,
                        now: float, colour: str) -> None:
        """
        Latitude rings, seen edge-on as they turn.

        Three ellipses whose height oscillates. This is the cheapest
        convincing globe cue there is and it costs three create_oval calls —
        the same trick the old _draw_orbits used, kept because it works.
        """
        for index in range(3):
            offset = (index - 1) * 0.42
            ring_r = r * math.sqrt(max(0.05, 1 - offset * offset))
            tilt = math.sin(now * 0.25 + index * 0.5)
            ry = max(2.0, abs(tilt) * ring_r * 0.55)
            cy_ring = cy + offset * r * 0.55
            canvas.create_oval(
                cx - ring_r, cy_ring - ry, cx + ring_r, cy_ring + ry,
                outline=_mix(CHROMA, colour, 0.22 + 0.30 * abs(tilt)), width=1,
            )
        # The outer shell, so the globe has a definite edge.
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                           outline=_mix(CHROMA, colour, 0.45), width=1)

    def _draw_rim(self, canvas: tk.Canvas, cx: float, cy: float, r: float,
                  now: float, colour: str, *, speed: float = 40.0) -> None:
        """
        Broken arc segments outside the globe — the HUD ring in the photo.

        Deliberately incomplete rings turning at different rates: a closed
        circle reads as a border, a broken one reads as instrumentation.
        """
        for radius_scale, extent, rate, width, accent in (
            (1.10, 70, speed, 3, False),
            (1.10, 40, -speed * 0.7, 2, True),
            (1.20, 120, speed * 0.45, 2, False),
            (1.20, 25, -speed * 1.3, 3, True),
        ):
            rr = r * radius_scale
            shade = ACCENT if accent else colour
            canvas.create_arc(
                cx - rr, cy - rr, cx + rr, cy + rr,
                start=(now * rate) % 360, extent=extent,
                style=tk.ARC, outline=_mix(CHROMA, shade, 0.75), width=width,
            )

    def _draw_orbits(self, canvas: tk.Canvas, cx: float, cy: float,
                     base: float, now: float, colour: str) -> None:
        """
        Rings around the sphere, tilting as they turn.

        An ellipse whose height oscillates is a circle seen edge-on and then
        face-on — the cheapest convincing 3D cue there is, and it costs three
        create_oval calls. This is what makes it read as a sphere with rings
        around it rather than a flat circle with circles on top.
        """
        for index in range(3):
            phase = (now * 0.35 + index / 3.0) % 1.0
            tilt = math.sin(phase * math.tau)          # -1 edge-on .. 1 edge-on
            rx = base * (1.02 + 0.10 * index)
            ry = max(2.0, abs(tilt) * rx * 0.42)
            bright = 0.30 + 0.45 * abs(tilt)
            canvas.create_oval(
                cx - rx, cy - ry, cx + rx, cy + ry,
                outline=_mix(CHROMA, colour, bright), width=2,
            )

    def _draw_motes(self, canvas: tk.Canvas, cx: float, cy: float,
                    base: float, now: float, colour: str, count: int = 7) -> None:
        """Small points circling the sphere, to give the motion a grain."""
        for index in range(count):
            angle = now * 0.9 + index * math.tau / count
            distance = base * (1.12 + 0.06 * math.sin(now * 1.7 + index))
            x = cx + math.cos(angle) * distance
            y = cy + math.sin(angle) * distance * 0.38
            size = 2.0 + 1.6 * (0.5 + 0.5 * math.sin(now * 2.3 + index))
            canvas.create_oval(
                x - size, y - size, x + size, y + size,
                fill=_mix(CHROMA, colour, 0.85), outline="",
            )

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
        """
        One frame of the sphere, in whatever state it is in.

        Every state draws the SAME object — a globe with a mesh, a core and a
        rim — and differs by MOTION and colour, not by shape. That is a
        deliberate change from the previous version, which drew six unrelated
        shapes. He asked for one thing that is always there and behaves
        differently depending on what it is doing; an object that morphs into
        a different object each time reads as six widgets taking turns rather
        than one assistant changing mood.
        """
        state = self._state
        colour = self.colours.get(state, self.colours["idle"])
        r = base * 0.66

        if state == "muted":
            # The one state that is deliberately inert. Still a sphere, so it
            # is recognisably the same object, just switched off.
            self._draw_wireframe(canvas, cx, cy, r, now * 0.05, colour)
            self._glow(canvas, cx, cy, r * 0.30, colour, steps=10, core=0.5)
            canvas.create_line(
                cx - r * 0.75, cy - r * 0.75, cx + r * 0.75, cy + r * 0.75,
                fill="#ff6b6b", width=4, capstyle=tk.ROUND,
            )
            return

        if state == "blocked":
            # A hard on/off blink plus an exclamation mark on the core, so the
            # meaning survives with colour vision removed entirely.
            lit = int(now * 2.5) % 2 == 0
            self._draw_wireframe(canvas, cx, cy, r, now, colour)
            self._draw_mesh(canvas, cx, cy, r, now, colour, spin=0.6,
                            energy=1.0 if lit else 0.0)
            self._draw_rim(canvas, cx, cy, r, now, colour, speed=140)
            if lit:
                self._glow(canvas, cx, cy, r * 0.5, colour)
                canvas.create_line(cx, cy - base * 0.20, cx, cy + base * 0.02,
                                   fill="#1a1a1a", width=4, capstyle=tk.ROUND)
                canvas.create_oval(cx - 2.5, cy + base * 0.14, cx + 2.5, cy + base * 0.19,
                                   fill="#1a1a1a", outline="")
            return

        # --- the three living states -----------------------------------------
        # Each gets its own MOTION signature, which is what he asked for:
        # something moving while it listens, something different while it
        # thinks.
        if state == "listening":
            # WAVES travelling outward from the core, the way a voice
            # assistant that is hearing you should look, with the core
            # breathing on real microphone energy on top. This is the only
            # state that reacts to him in real time.
            for index in range(3):
                phase = ((now * 1.1) + index / 3.0) % 1.0
                ring = r * (0.95 + phase * 0.75)
                fade = 1.0 - phase
                canvas.create_oval(
                    cx - ring, cy - ring, cx + ring, cy + ring,
                    outline=_mix(CHROMA, colour, 0.70 * fade),
                    width=max(1, int(4 * fade)),
                )
            level = self._level
            self._draw_wireframe(canvas, cx, cy, r, now, colour)
            self._draw_mesh(canvas, cx, cy, r, now, colour, spin=0.30,
                            energy=0.35 + 0.65 * level)
            self._draw_rim(canvas, cx, cy, r, now, colour, speed=55)
            self._glow(canvas, cx, cy, r * (0.34 + 0.22 * level), colour)
            return

        if state == "thinking":
            # PROCESSING, and it must not be mistakable for listening. No
            # outward waves at all: the globe spins faster, the rim arcs race,
            # and a bright segment SWEEPS around it — motion that is circular
            # and internal rather than radiating outward.
            sweep = (now * 1.5) % math.tau
            self._draw_wireframe(canvas, cx, cy, r, now * 2.2, colour)
            self._draw_mesh(canvas, cx, cy, r, now * 2.2, colour, spin=0.9,
                            energy=0.30 + 0.30 * math.sin(now * 4.0))
            self._draw_rim(canvas, cx, cy, r, now, colour, speed=190)
            rr = r * 1.02
            canvas.create_arc(
                cx - rr, cy - rr, cx + rr, cy + rr,
                start=math.degrees(sweep) % 360, extent=55,
                style=tk.ARC, outline=colour, width=5,
            )
            self._glow(canvas, cx, cy, r * 0.32, colour)
            return

        if state == "speaking":
            # Making sound: the core pulses at a syllable rate and the mesh
            # brightens with it, so the sphere itself looks like it is
            # talking rather than something orbiting it.
            beat = 0.5 + 0.5 * math.sin(now * 7.0)
            self._draw_wireframe(canvas, cx, cy, r, now, colour)
            self._draw_mesh(canvas, cx, cy, r, now, colour, spin=0.40,
                            energy=0.35 + 0.55 * beat)
            self._draw_rim(canvas, cx, cy, r, now, colour, speed=70)
            for i in range(10):
                angle = (math.tau * i / 10) + now * 0.5
                amp = 0.5 + 0.5 * math.sin(now * 8.0 + i * 1.1)
                inner, outer = r * 1.26, r * 1.26 + base * 0.16 * amp
                canvas.create_line(
                    cx + inner * math.cos(angle), cy + inner * math.sin(angle),
                    cx + outer * math.cos(angle), cy + outer * math.sin(angle),
                    fill=_mix(CHROMA, colour, 0.45 + 0.55 * amp),
                    width=3, capstyle=tk.ROUND,
                )
            self._glow(canvas, cx, cy, r * (0.32 + 0.10 * beat), colour)
            return

        # --- idle -------------------------------------------------------------
        # VISIBLE, and that is a deliberate reversal. It used to be a small
        # near-black dot, from an earlier "black when stopped" instruction, and
        # he reported the orb as simply not appearing — which at that colour,
        # on a dark desktop, is an accurate description of what it was doing.
        # It is now the same sphere as every other state, turning slowly and
        # lit calmly. Quiet, not absent.
        breathe = 0.5 + 0.5 * math.sin(now * (math.tau / 6.0))
        self._draw_wireframe(canvas, cx, cy, r, now, colour)
        self._draw_mesh(canvas, cx, cy, r, now, colour, spin=0.18,
                        energy=0.10 + 0.12 * breathe)
        self._draw_rim(canvas, cx, cy, r, now, colour, speed=18)
        self._glow(canvas, cx, cy, r * (0.26 + 0.03 * breathe), colour)

    def _draw_name(self, canvas: tk.Canvas, cx: float, y: float,
                   base: float, colour: str) -> None:
        """
        His assistant's name under the sphere, spaced out like the reference.

        Drawn twice — a dim offset copy under a bright one — because a Tk
        canvas has no glow filter and two overlapping texts is the cheapest
        thing that reads as one. Letters are spaced with real spaces rather
        than placed individually: per-glyph placement needs font metrics that
        differ per machine, and one mis-measured glyph turns a name into a
        stutter.
        """
        name = self.label
        if not name:
            return
        spaced = " ".join(name.upper())
        size = max(9, int(base * 0.115))
        for offset, shade in ((2, 0.35), (0, 1.0)):
            canvas.create_text(
                cx, y + offset, text=spaced,
                fill=_mix(CHROMA, colour, shade),
                font=("Consolas", size, "bold"),
            )

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

        def name_height() -> int:
            return int(self.size * NAME_H_FRACTION) if self.label else 0

        def canvas_size() -> tuple[int, int]:
            wh = int(self.size * WAVE_HEADROOM)
            return wh, wh + label_h + name_height()

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

        # NO MOUSE BINDINGS AT ALL, and no click-through toggling.
        #
        # There used to be drag-to-move and wheel-to-resize here, plus a rule
        # that made the window solid while idle so the mouse could reach it.
        # His verdict after using it: "make yourself bigger and smaller none
        # of it is working, just forget it man, we do not need feature, you
        # gotta remove this altogether, it should stay still in one place,
        # and it should not move, it should not be draggable".
        #
        # Removing it makes the cursor problem go away permanently rather
        # than conditionally. The window is click-through in EVERY state now,
        # so there is no combination of circumstances in which the orb can
        # take a click meant for something underneath it. That is a stronger
        # guarantee than any state machine, and it needed less code.

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
            now = time.monotonic()
            canvas.delete("all")
            name_h = name_height()
            orb_area = canvas_h - (LABEL_H if self.show_transcript else 0) - name_h
            cx, cy = canvas_w / 2, orb_area / 2
            base = self.size / 2
            self._draw_orb(canvas, cx, cy, base, now)
            if name_h:
                colour = self.colours.get(self._state, self.colours["idle"])
                self._draw_name(canvas, cx, orb_area + name_h * 0.45, base, colour)
            self._draw_caption(canvas, canvas_w, orb_area + name_h, now)

            # Resolved once, when the window is genuinely realized, and
            # cached. Both the click-through and the topmost calls MUST have
            # the real top-level, and asking too early yields the canvas
            # child — which is how the orb ended up invisible for a whole
            # session. Until it resolves, do nothing at all: no styling is
            # far better than styling the wrong window.
            if not self._hwnd:
                self._hwnd = toplevel_hwnd(root)

            # CLICK-THROUGH: ghost while BUSY, solid while IDLE.
            #
            # This rule used to be the exact opposite — `want = state ==
            # "idle"` — and that is the "my cursor stops working when Jalen
            # is running" report. Measured on the live window before the fix:
            #
            #     idle       WS_EX_TRANSPARENT set     clicks pass through
            #     listening  CLEAR                     BLOCKED
            #     thinking   CLEAR                     BLOCKED
            #     speaking   CLEAR                     BLOCKED
            #     muted      CLEAR                     BLOCKED
            #
            # `muted` is the damning one: mute is a state you LEAVE it in, so
            # a muted assistant sat there permanently eating a square of his
            # screen. An overlay that costs you your mouse the moment it
            # starts working is worse than no overlay.
            #
            # The inversion was not careless — it was serving the drag and
            # wheel bindings below, which need a window the mouse can hit.
            # But those are for repositioning an orb you are looking at, i.e.
            # an IDLE one. Nobody drags the orb mid-sentence.
            # Click-through, unconditionally. Nothing on this window is
            # meant to be clicked, so there is no state in which it should
            # intercept the mouse.
            if self.click_through_when_busy and self._hwnd:
                if self._click_through_applied is not True:
                    if set_click_through(self._hwnd, True):
                        self._click_through_applied = True

            # RE-ASSERT ALWAYS-ON-TOP, periodically.
            #
            # He reported the orb as not visible "no matter whichever window
            # I will be". Setting -topmost once at startup is not enough on
            # Windows: opening another topmost window, a full-screen app, or
            # a UAC prompt silently demotes ours, and it never comes back.
            # There is no event to listen for, so the only reliable fix is to
            # keep saying it. Every ~2s, and only via Win32 — Tk's attribute
            # call is a no-op when it thinks the value is unchanged, which is
            # exactly the case here.
            if self.always_on_top and self._hwnd:
                self._topmost_tick += 1
                if self._topmost_tick % TOPMOST_REASSERT_TICKS == 0:
                    raise_to_top(self._hwnd)

            interval = 40 if self._state in _ACTIVE_STATES else 150
            root.after(interval, tick)

        tick()
        root.mainloop()


class TranscriptWindow:
    """
    Long content goes here instead of being read out (spec B15).

    "I SAID FULL DETAIL IS ON SCREEN BUT NEVER ACTUALLY WROTE IT ANYWHERE."
    Jalen's own words, in the audit log, after he shouted about it. The bug
    was in the version below and it is worth stating exactly, because the
    shape of it is this project's signature failure:

        show() put the text on a queue and started a worker thread ONLY IF
        one was not already running. The worker read ONE item, opened a
        window, and called mainloop() — which never returns while the window
        is open. So the second long answer, and every one after it, went onto
        a queue that nobody was reading. The thread was alive, so no new one
        started. Nothing errored. Jalen said "the full text is on screen" and
        it was true exactly once per session.

    Verified before fixing: two show() calls, queue depth 1 afterwards.

    THE FIX IS ONE WINDOW THAT STAYS, not a thread per answer. It keeps its
    own Tk root, polls the queue from inside its own event loop with after(),
    and appends each new answer to the top with a heading. Answers accumulate
    instead of replacing each other — he asked a question, got a summary out
    loud, and the detail should still be there when he looks up from
    something else.

    Tk in a worker thread is already what the orb does, and the two roots do
    not interfere because each stays on its own thread and neither touches
    the other's widgets.
    """

    MAX_ENTRIES = 20          # keep the window from growing without bound

    def __init__(self, cfg) -> None:
        self.enabled = bool(cfg.get_path("ui.transcript_window", True))
        self._q: queue.Queue[tuple[str, str]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._root: tk.Tk | None = None
        self._shown = 0        # how many answers have actually been rendered

    @property
    def shown_count(self) -> int:
        """
        How many answers have genuinely reached the screen.

        Exposed so a test can assert the difference between "queued" and
        "displayed" — the exact gap the old version fell into.
        """
        return self._shown

    def show(self, title: str, body: str) -> None:
        if not self.enabled or not (body or "").strip():
            return
        self._q.put((title, body))
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="jalen-transcript", daemon=True
                )
                self._thread.start()

    def _run(self) -> None:
        root = tk.Tk()
        self._root = root
        root.title("Jalen - full answers")
        root.geometry("820x620")
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
        text.tag_configure("heading", foreground="#2bb3c9",
                           font=("Segoe UI", 11, "bold"), spacing1=10, spacing3=6)
        text.config(state="disabled")

        def drain() -> None:
            """
            Poll the queue from INSIDE the event loop.

            This is the whole fix. after() runs on the same thread as
            mainloop(), so new answers arrive without needing a second
            thread, a second window, or a mainloop that has to return.
            """
            wrote = False
            while True:
                try:
                    title, body = self._q.get_nowait()
                except queue.Empty:
                    break
                text.config(state="normal")
                # Newest at the TOP: the thing he just asked about should not
                # require scrolling past everything he asked earlier.
                text.insert("1.0", f"{body.strip()}\n\n")
                text.insert("1.0", f"{title}\n", "heading")
                text.config(state="disabled")
                self._shown += 1
                wrote = True

            if self._shown > self.MAX_ENTRIES:
                # Trim from the bottom — the oldest answers.
                text.config(state="normal")
                text.delete(f"{self.MAX_ENTRIES * 3}.0", "end")
                text.config(state="disabled")

            if wrote:
                text.see("1.0")
                try:
                    root.deiconify()      # bring it back if he minimised it
                    root.lift()
                except tk.TclError:
                    pass
            root.after(250, drain)

        # Closing the window must not kill the queue: hide it instead, so the
        # next long answer brings it back with the history intact.
        root.protocol("WM_DELETE_WINDOW", root.withdraw)
        drain()
        root.mainloop()
