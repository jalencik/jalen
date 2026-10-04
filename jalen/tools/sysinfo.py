"""
"What is eating my disk and memory, and what can I safely delete?"

Three read-only reports, built around one hard constraint: this module NEVER
deletes anything, no matter how confidently it can name a safe-to-delete
folder. Deletion already exists — filesystem.delete_file — and it's RED,
gated on a spoken "yes". Duplicating a delete path here, even a "confirmed
safe" one, would create a second way to destroy files that the safety model
never rebalanced around. cleanup_suggestions() only ever measures and reports.

Every walk in this file is bounded two ways at once, not one: a hard entry
count AND a wall-clock time budget. An entry cap alone still hangs on a
folder full of gigantic files (few entries, huge stat() latency on a slow or
network-backed disk); a time budget alone still means an unpredictable
number of entries scanned run to run. Both together is what "can never hang"
actually requires. When either limit is hit mid-walk, the report says so —
silently under-counting and presenting it as complete would be exactly the
kind of fabricated-success bug launcher.py's docstring warns about.

never_touch enforcement: SafetyEngine.classify() already blocks any tool
call that names a never_touch path directly, but that's argument-level — it
does nothing for a folder these tools discover themselves while walking
(e.g. Desktop/credentials, if that ever existed). So every walk here also
prunes never_touch directories from os.walk's own dirnames, reusing
filesystem.py's exact predicate rather than re-deriving it — two independent
implementations of "is this the protected path" is how they'd quietly drift
apart. C:\\Windows itself is on that list, which is also why
cleanup_suggestions() never scans for Windows Update leftovers: it names
that category but reports no number for it, rather than reading inside a
folder the config says not to touch.

disk_report() answers "what is filling my C drive" with a walk of the WHOLE
drive (DriveScan, below), not of four user folders. The same rule holds, so
Windows itself is never entered; what is left over once everything readable
has been counted is reported as one sized line, "Windows and other system
files I don't look inside", rather than guessed about. The walk adds up the
sizes the directory listing already carries: it never opens a file.
"""
from __future__ import annotations

import ctypes
import heapq
import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, NamedTuple

from .filesystem import _is_under_never_touch, _never_touch_dirs
from .system import IS_WINDOWS

# Bounds for the user-folder walk (disk_report). 200k entries covers even a
# neglected Downloads folder with years of installers in it; the time budget
# is what actually protects against a slow disk or a network-backed Temp
# path where 200k stat() calls could otherwise take minutes.
#
# Since the whole-drive scan below this is only the FALLBACK, used when that
# scan cannot read the drive at all. It was the whole of disk_report until
# 2026-10-01, when it explained 0.7 GB of the 139 GB in use.
MAX_WALK_ENTRIES = 200_000
WALK_TIME_BUDGET_S = 12.0

# ------------------------------------------------------- the whole-drive scan
# MEASURED 2026-10-01 on this machine, nothing opened, sizes only, at normal
# priority, other work running: one walk of C: from the root, with C:\Windows,
# WindowsApps and Defender left out as the config requires, covered 954,982 files
# in 139,205 folders in 131.4 s (3 unreadable) and explained 119.9 GB of the
# 149.3 GB in use; the other 29.5 GB is what it may not open, plus System Volume
# Information, which is access-denied. An earlier walk, before MAX_PATH_CHARS,
# was still going at 600 s with 118.7 GB counted: it was stuck in one chain of
# 4,000 nested folders.
#   - The budget is 4.6x the 131 s, so a machine several times as busy still
#     finishes. NOT MEASURED on a slower disk; past the budget the answer says it
#     stopped and how much it covered.
#   - The entry cap is 4.2x the files measured. It exists for a drive that is
#     not like this one (a network share, a runaway tree); memory does not depend
#     on it because only folders are remembered, not files.
DEEP_SCAN_BUDGET_S = 600.0
MAX_DEEP_ENTRIES = 4_000_000
# Folders deeper than this are added into their ancestor at this depth, so the
# map of folders stays small however deep a disk goes. Six levels reach
# Users/<name>/AppData/Local/<vendor>/<product> and
# Users/<name>/.cache/huggingface/hub/<model>. MEASURED: the whole of this C: came
# to 6,108 folders in the map and the process grew from 44 to 45 MB at its peak.
MAX_TREE_DEPTH = 6
# A folder whose path is longer than this is not entered (and is counted with
# the folders that could not be read). Windows' own limit for ordinary programs
# is 260 characters; 4x that still reaches anything a real program made. MEASURED
# 2026-10-01: listing the one chain on this C: that is longer, 4,000 levels and up
# to 22,942 characters, took 104 s by itself - every level costs more than the
# last, 1.4 s for the first 500 levels and 27 s for the last 500 - and it holds
# nothing but a test's empty folders.
MAX_PATH_CHARS = 1024
# Only a file at least this big is remembered by name. A judgement, not a
# measurement: below it a file cannot be what is filling a 157 GB drive.
BIG_FILE_MIN = 100_000_000
BIGGEST_FILES_KEPT = 12
# How many places the answer names.
TOP_PLACES = 5
# A folder is replaced by what is inside it, in the list of places, while one
# part of it holds at least this share of it. Below that the folder is reported
# whole: thirty 1 GB items in Downloads are "Downloads, 30 GB", not thirty lines.
# Chosen by reading the result of one real scan of this machine (2026-10-01): at
# 0.05, 0.10 and 0.20 the five places came out the same, each a specific folder
# (the Claude app's data 12.5 GB, its vm_bundles 11.5 GB, Chrome 8.2 GB ...); at
# 0.30 and up it stopped at "AppData Local, 49.2 GB", too coarse to be an answer.
# NOT MEASURED on any other machine.
SPLIT_SHARE = 0.20
# What was not counted is called "the system" only above this; below it it is
# rounding, hard links and files that came and went during the walk.
MIN_SYSTEM_REPORT_BYTES = 1_000_000_000
SYSTEM_PATH = "<system>"
# How long one ask waits for a scan that is still going. The known big places
# are walked first, and that is what the ask waits for; the rest of the walk goes
# on behind the answer. MEASURED 2026-10-01, the quick tier on this machine: 11 s
# on its own (.cache 1.9 s, pip cache 0.4 s, npm-cache 6.0 s, Packages 1.6 s,
# ProgramData 1.0 s), 17 s and 25 s in two runs with other work on the disk. 20 s
# is between those; a run that is slower than that answers with what it has, said
# to be partial. A drive with no known places has no quick tier and waits this
# long for the whole walk.
ASK_WAIT_S = 20.0
# The walk runs at normal priority for this long, because someone may be waiting
# on it, and then drops to background priority (CPU, memory and DISK) so it does
# not slow his laptop while he works. MEASURED 2026-10-01: the known places took
# 11 to 25 s at normal priority, but 88 s when the whole walk ran at background
# priority while other test runs used the same disk - which is why the quick tier
# is not run at low priority. The whole walk then took 131 s at normal priority
# and 252 s with the switch to background after 20 s; that pair is not a
# controlled comparison (focused tests ran beside the second), so it says only
# that the lowered walk still finishes well inside DEEP_SCAN_BUDGET_S. NOT
# MEASURED: how much the walk slows his own work at either priority.
FULL_SPEED_S = 20.0
# How long prewarm_system_scan leaves the drive alone so the speech and brain
# warmups can load their models first. 3x the nine seconds those were measured
# to take together; NOT MEASURED whether the walk would actually slow them.
PREWARM_DELAY_S = 30.0
# How old a finished count may be before an ask starts a new one behind the
# answer. An hour, not CACHE_TTL_S's ten minutes: a count is minutes of disk
# activity, so counting again after every ten minutes of asking would keep the
# drive busy for good, and what moves is the free space, which every answer reads
# live. NOT MEASURED: how fast the breakdown really changes. The answer says how
# old it is once it is past a minute and a half.
DEEP_SCAN_TTL_S = 3600.0

_REPARSE = 0x400                          # junction, symlink, mount point
_NOT_ON_DISK = 0x1000 | 0x40000 | 0x400000  # OFFLINE, RECALL_ON_OPEN, RECALL_ON_DATA_ACCESS

# ---------------------------------------------------------------- caching
# Measured on this machine: disk_report 12.8s, cleanup_suggestions 41.0s.
# For a voice assistant that is unusable — the answer arrives long after the
# question stopped mattering. But the underlying numbers barely move minute
# to minute: disk usage and cache sizes are not volatile.
#
# So: serve the previous answer instantly when it's recent, and refresh in
# the background so the NEXT ask is instant too. The first ask of a session
# still pays full price and says so; every ask after that is immediate.
# Freshness is stated out loud rather than hidden, because silently
# reporting a stale number as current is the kind of small lie that makes an
# assistant untrustworthy.
CACHE_TTL_S = 600.0          # 10 minutes: disk/cache sizes don't move faster
CACHE_STALE_AFTER_S = 3600.0  # beyond an hour, say the figure is old

_cache: dict[str, tuple[float, str]] = {}
_cache_lock = threading.Lock()
_refreshing: set[str] = set()


def _age_note(age_s: float) -> str:
    if age_s < 90:
        return ""
    minutes = int(age_s // 60)
    if minutes < 60:
        return f" (measured {minutes} minute{'s' if minutes != 1 else ''} ago)"
    hours = int(minutes // 60)
    return f" (measured {hours} hour{'s' if hours != 1 else ''} ago)"


def _cached(key: str, compute) -> str:
    """
    Instant answer from cache when we have a recent one; otherwise compute
    now. A cache hit past its TTL is still returned immediately, with a
    background refresh kicked off, so the user never waits on a scan whose
    answer we already roughly know.
    """
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)

    if entry is not None:
        computed_at, value = entry
        age = now - computed_at
        if age <= CACHE_TTL_S:
            return value
        # Stale but usable: answer now, refresh behind the scenes.
        with _cache_lock:
            already = key in _refreshing
            if not already:
                _refreshing.add(key)
        if not already:
            def refresh() -> None:
                try:
                    fresh = compute()
                    with _cache_lock:
                        _cache[key] = (time.monotonic(), fresh)
                except Exception:
                    pass  # a failed refresh must never break the caller
                finally:
                    with _cache_lock:
                        _refreshing.discard(key)

            threading.Thread(target=refresh, name=f"sysinfo-{key}", daemon=True).start()
        if age <= CACHE_STALE_AFTER_S:
            return value + _age_note(age)
        return value + _age_note(age) + " I'm re-checking now — ask again in a moment for current figures."

    # Nothing cached yet. These scans take 13s (disk) and 41-92s (cleanup)
    # depending on how cold the filesystem cache is — far too long to make
    # someone wait mid-conversation. Answer immediately with what we can say
    # for certain, and compute in the background so the next ask is instant.
    # prewarm_system_scan() means this branch is normally never hit at all;
    # it only fires if the question arrives during the first seconds of a
    # session, before warmup finished.
    with _cache_lock:
        already = key in _refreshing
        if not already:
            _refreshing.add(key)
    if not already:
        def compute_now() -> None:
            try:
                fresh = compute()
                with _cache_lock:
                    _cache[key] = (time.monotonic(), fresh)
            except Exception:
                pass
            finally:
                with _cache_lock:
                    _refreshing.discard(key)

        threading.Thread(target=compute_now, name=f"sysinfo-first-{key}", daemon=True).start()

    quick = _instant_summary(key)
    tail = " I'm still measuring the details — ask again in a few seconds for the full picture."
    if key == "cleanup":
        # The "nothing gets deleted" reassurance belongs on EVERY cleanup
        # answer, including this partial one — that promise is the whole
        # reason this tool is safe to run without asking, and dropping it
        # from one code path would be exactly the kind of quiet
        # inconsistency that erodes trust in it.
        tail += " I'm only reporting sizes; nothing gets deleted unless you tell me to."
    return quick + tail


def _instant_summary(key: str) -> str:
    """Something true and useful RIGHT NOW, while the real scan runs."""
    try:
        drives = _drive_usage()
    except Exception:
        return "Give me a moment to check."
    if not drives:
        return "Give me a moment to check."
    parts = []
    for d in drives[:2]:
        parts.append(
            f"your {d['label']} has {_size_str(d['free'])} free out of "
            f"{_size_str(d['total'])}, {d['percent']:.0f} percent full"
        )
    return ("Right now " + "; ".join(parts) + ".")


def refresh_system_scan() -> str:
    """Force a fresh scan in the background — GREEN."""
    with _cache_lock:
        _cache.clear()
    _rescan(_system_root())
    threading.Thread(
        target=lambda: _cached("cleanup", _cleanup_uncached), name="sysinfo-warm-cleanup", daemon=True
    ).start()
    return ("Re-checking your disk and cleanup figures now. Counting the whole drive takes "
            "a few minutes, so ask again then.")


def prewarm_system_scan() -> None:
    """
    Run the expensive scans once at startup, off the critical path, so the
    first time the user actually asks the answer is already sitting there.
    Called by Jalen.prewarm() alongside the STT/TTS/brain warmups, and by
    run.py for text and Telegram modes, which never call prewarm().
    """
    def warm_drive() -> None:
        # Not at once: the speech and brain warmups load models from the same
        # disk in about nine seconds (the figure in Jalen.prewarm), and a
        # whole-drive walk starting beside them is the one thing here that could
        # slow "ready". If he asks sooner, disk_report starts the scan itself.
        time.sleep(PREWARM_DELAY_S)
        try:
            _scan_for(_system_root())
        except Exception:
            pass

    def warm_cleanup() -> None:
        try:
            _cached("cleanup", _cleanup_uncached)
        except Exception:
            pass

    threading.Thread(target=warm_drive, name="sysinfo-prewarm-drive", daemon=True).start()
    threading.Thread(target=warm_cleanup, name="sysinfo-prewarm-cleanup", daemon=True).start()

# Bounds for each individual cleanup-category scan (Temp, a browser cache,
# etc). Smaller than the disk_report budget because cleanup_suggestions()
# runs several of these back to back in one call — the total time has to
# stay conversational, not just each piece.
MAX_CATEGORY_ENTRIES = 60_000
CATEGORY_TIME_BUDGET_S = 5.0


def _size_str(num_bytes: float) -> str:
    """Human, spoken-friendly size — 'gigabytes'/'megabytes', not 'GB'/'MB',
    matching how get_system_status already speaks sizes in system.py."""
    num_bytes = max(0.0, float(num_bytes))
    if num_bytes >= 1e9:
        return f"{num_bytes / 1e9:.1f} gigabytes"
    if num_bytes >= 1e6:
        return f"{num_bytes / 1e6:.0f} megabytes"
    if num_bytes >= 1e3:
        return f"{num_bytes / 1e3:.0f} kilobytes"
    return f"{num_bytes:.0f} bytes"


# --------------------------------------------------------------------- disk
def _drive_usage() -> list[dict[str, Any]]:
    import psutil

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for part in psutil.disk_partitions(all=False):
        if part.mountpoint in seen:
            continue
        opts = (part.opts or "").lower()
        if "cdrom" in opts or not part.fstype:
            continue  # empty optical drives etc. raise on disk_usage anyway
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        seen.add(part.mountpoint)
        letter = part.mountpoint.rstrip("\\/")
        label = f"{letter[0].upper()} drive" if len(letter) == 2 and letter[1] == ":" else letter
        out.append({
            "label": label,
            "mountpoint": part.mountpoint,
            "total": usage.total,
            "free": usage.free,
            "percent": usage.percent,
        })
    return out


def _is_a_link(path: Any) -> bool:
    """
    A junction or a symlink: what is inside it lives somewhere else, often on
    another drive. foldermove leaves one at the old path after a move
    ("move my Downloads to D"), and os.walk does NOT treat a junction as a
    link - it walks straight through. Counted here, D:'s files would be
    reported as C:'s usage, and "Downloads at 4.2 GB" would contradict the
    sentence that said the move freed 4.2 GB on C.
    """
    try:
        return os.path.islink(path) or getattr(os.path, "isjunction", lambda p: False)(path)
    except OSError:
        return False


def _walk_roots() -> list[Path]:
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        home / "AppData" / "Local" / "Temp",
    ]
    return [p for p in candidates if p.is_dir() and not _is_a_link(p)]


def _scan_user_folders(
    roots: list[Path],
    never_dirs: list[str],
    max_entries: int = MAX_WALK_ENTRIES,
    time_budget_s: float = WALK_TIME_BUDGET_S,
) -> tuple[dict[str, int], list[tuple[int, str]], bool, int]:
    """
    One bounded pass over `roots`. Returns:
      - folder_sizes: total bytes per immediate child of each root (files
        sitting directly in a root are bucketed under the root itself) —
        this is a "du -d 1" per watched folder, not a full recursive tree,
        because a full tree for every directory is what the entry cap and
        time budget exist to avoid.
      - biggest_files: (size, path) for the largest individual files seen,
        largest first.
      - capped: True if the entry cap or time budget cut the walk short.
      - scanned: how many files were actually examined.
    """
    folder_sizes: dict[str, int] = {}
    biggest_files: list[tuple[int, str]] = []
    scanned = 0
    capped = False
    deadline = time.monotonic() + time_budget_s

    for root in roots:
        if capped:
            break
        if _is_a_link(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if not _is_under_never_touch(Path(dirpath) / d, never_dirs)
                and not _is_a_link(Path(dirpath) / d)
            ]
            try:
                rel_parts = Path(dirpath).relative_to(root).parts
            except ValueError:
                rel_parts = ()
            bucket = str(root / rel_parts[0]) if rel_parts else str(root)

            for name in filenames:
                scanned += 1
                if scanned > max_entries or time.monotonic() > deadline:
                    capped = True
                    break
                fp = Path(dirpath) / name
                try:
                    size = fp.stat().st_size
                except OSError:
                    continue
                folder_sizes[bucket] = folder_sizes.get(bucket, 0) + size
                biggest_files.append((size, str(fp)))
            if capped:
                break
        if capped:
            break

    biggest_files.sort(key=lambda t: t[0], reverse=True)
    return folder_sizes, biggest_files[:10], capped, scanned


def _user_folder_summary() -> str:
    """
    What is using space in the user's own folders (Desktop, Documents,
    Downloads, Temp) - the old disk_report, kept for when the whole-drive scan
    cannot read the drive at all. Says that is all it looked at.
    """
    roots = _walk_roots()
    if not roots:
        return " I couldn't read the whole drive, and I couldn't find your Desktop, Documents, Downloads or Temp folders to check either."

    never_dirs = _never_touch_dirs()
    folder_sizes, biggest_files, capped, scanned = _scan_user_folders(roots, never_dirs)
    summary = (" I couldn't read the whole drive, so this is only your Desktop, Documents, "
               "Downloads and Temp folders.")

    top_folders = sorted(folder_sizes.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top_folders = [(name, size) for name, size in top_folders if size > 0]
    if top_folders:
        bits = ", ".join(f"{Path(name).name} at {_size_str(size)}" for name, size in top_folders)
        summary += f" The biggest things in them: {bits}."

    if biggest_files:
        f_size, f_path = biggest_files[0]
        summary += f" The single largest file is {Path(f_path).name} at {_size_str(f_size)}."

    if capped:
        summary += f" I stopped after checking {scanned:,} items, so there's likely more beyond this."

    return summary


# ================================================================ whole drive
class Snapshot(NamedTuple):
    """What a DriveScan knows at one moment. Plain data, safe to hold."""

    state: str                          # running | done | budget | entries | failed
    root: str
    counted: int                        # bytes in the files seen so far
    files: int
    dirs: int
    denied: int                         # folders and files it was not allowed to read
    elapsed_s: float
    own: dict                           # folder -> bytes in the files directly inside it
    biggest: list                       # (size, path) of the largest files, largest first
    error: str


class Place(NamedTuple):
    """One line of the answer: a folder, or the files sitting loose in one."""

    path: str
    size: int
    loose: bool = False


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _enter_background_mode(begin: bool) -> bool:
    """
    Lower this thread's CPU, memory AND disk priority for the length of the
    walk (Windows THREAD_MODE_BACKGROUND_BEGIN / _END), so counting a drive
    behind the answer does not make his laptop crawl while he works. A private
    kernel32 handle, so the argument types set here are not set on the one the
    rest of the process shares. Best effort: False if it is not available.
    """
    if not IS_WINDOWS:
        return False
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentThread.restype = ctypes.c_void_p
        kernel.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        return bool(kernel.SetThreadPriority(kernel.GetCurrentThread(),
                                             0x00010000 if begin else 0x00020000))
    except Exception:
        return False


class DriveScan:
    """
    One background walk of a drive from its root, adding up sizes.

    It never opens a file: sizes come from the directory listing. It does not
    follow junctions, symlinks or mount points (a folder moved to D: leaves a
    junction behind, and what is behind it is not this drive's), skips files
    that are only a placeholder for something in the cloud, and does not enter
    the never-touch folders - Windows itself is one - so the caller is told how
    much of the drive that leaves.

    `first` are places that usually hold the space (caches, virtual disks);
    they are walked before the rest so that an early answer already says
    something real, and `quick_done` is set when they are finished. With none,
    `quick_done` is only set at the end. Whatever the reason a scan stops, `done`
    is set BEFORE `quick_done` on that last step, so a reader woken by
    `quick_done` can tell the early signal from the final one by `done`.

    Memory is bounded by `max_depth`: bytes in a folder deeper than that are
    added to its ancestor at that depth.
    """

    def __init__(
        self,
        root: str,
        never_dirs=(),
        first=(),
        budget_s: float = DEEP_SCAN_BUDGET_S,
        max_entries: int = MAX_DEEP_ENTRIES,
        max_depth: int = MAX_TREE_DEPTH,
        big_file_min: int = BIG_FILE_MIN,
        max_path: int = MAX_PATH_CHARS,
    ) -> None:
        self.root = os.path.abspath(root)
        self._max_path = max_path
        # Only the never-touch folders under THIS root can match, and only a
        # folder as shallow as the deepest of them needs asking: the check is
        # string work on the whole path, and one folder on this C: is 4,007
        # levels (about 18,000 characters) down, where doing it at every level
        # was the slowest part of the walk.
        base = self.root.replace("\\", "/").rstrip("/").lower()
        self._never = [d for d in never_dirs if d == base or d.startswith(base + "/")]
        self._never_depth = max((d[len(base):].count("/") for d in self._never), default=0)
        self._walked_depth = 0                        # how deep the places walked first go
        self._budget_s = budget_s
        self._max_entries = max_entries
        self._max_depth = max_depth
        self._big_file_min = big_file_min
        self._lock = threading.Lock()
        self._own: dict[str, int] = {}
        self._biggest: list[tuple[int, str]] = []     # a min-heap, so the smallest kept is cheap to drop
        self._counted = 0
        self._files = 0
        self._dirs = 0
        self._denied = 0
        self._entries = 0
        self._state = "idle"
        self._error = ""
        self._stop = ""
        self._lowered = False
        self._deadline = 0.0
        self._walked: set[str] = set()                # places already walked, normalised
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.quick_done = threading.Event()
        self.done = threading.Event()
        self._thread: threading.Thread | None = None
        self._first = [
            p for p in dict.fromkeys(os.path.abspath(str(x)) for x in first)
            if self._under_root(_norm(p)) and os.path.isdir(p) and not _is_a_link(p)
        ]

    def _under_root(self, norm: str) -> bool:
        root = _norm(self.root)
        return norm == root or norm.startswith(root.rstrip(os.sep) + os.sep)

    # ------------------------------------------------------------- control
    def start(self) -> threading.Thread:
        if self._thread is None:
            self._state = "running"
            self.started_at = time.monotonic()
            self._thread = threading.Thread(target=self.run, name=f"sysinfo-scan-{self.root}", daemon=True)
            self._thread.start()
        return self._thread

    def run(self) -> None:
        if self.started_at is None:
            self.started_at = time.monotonic()
        self._deadline = self.started_at + self._budget_s
        with self._lock:
            self._state = "running"
        final, error = "done", ""
        try:
            with os.scandir(self.root):
                pass                                   # not readable at all: say so, do not "finish"
            for place in self._first:
                if self._stop:
                    break
                self._walk_from(place)
            if self._first:
                self.quick_done.set()
            if not self._stop:
                self._walk_from(self.root)
            final = self._stop or "done"
        except Exception as exc:                       # noqa: BLE001 - reported, not raised
            final, error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            if self._lowered:
                _enter_background_mode(False)
            self.finished_at = time.monotonic()
            with self._lock:
                self._state, self._error = final, error
            self.done.set()                            # BEFORE quick_done: see the class docstring
            self.quick_done.set()

    def snapshot(self) -> Snapshot:
        with self._lock:
            end = self.finished_at if self.finished_at is not None else time.monotonic()
            return Snapshot(
                state=self._state,
                root=self.root,
                counted=self._counted,
                files=self._files,
                dirs=self._dirs,
                denied=self._denied,
                elapsed_s=(end - self.started_at) if self.started_at is not None else 0.0,
                own=dict(self._own),
                biggest=sorted(self._biggest, reverse=True),
                error=self._error,
            )

    # ---------------------------------------------------------------- walk
    def _covered(self, norm: str) -> bool:
        return any(norm == d or norm.startswith(d + os.sep) for d in self._walked)

    def _walk_from(self, start: str) -> None:
        norm = _norm(start)
        if self._covered(norm):
            return                                      # inside a place that was already counted
        try:
            parts = Path(start).relative_to(self.root).parts
        except ValueError:
            parts = ()
        if norm != _norm(self.root):
            self._walked.add(norm)
            self._walked_depth = max(self._walked_depth, len(parts))
        key = start if len(parts) <= self._max_depth else os.path.join(self.root, *parts[: self._max_depth])
        stack = [(start, key, len(parts))]
        while stack and not self._stop:
            path, key, depth = stack.pop()
            self._scan_one(path, key, depth, stack)

    def _scan_one(self, path: str, key: str, depth: int, stack: list) -> None:
        now = time.monotonic()
        if now > self._deadline:
            self._stop = "budget"
            return
        if not self._lowered and now - (self.started_at or now) > FULL_SPEED_S:
            # Whoever asked has had their wait; from here this is a chore, so
            # it gives way to everything he is actually doing.
            self._lowered = _enter_background_mode(True)
        try:
            entries = os.scandir(path)
        except OSError:
            with self._lock:
                self._denied += 1
            return
        own = files = denied = 0
        kids: list[str] = []
        big: list[tuple[int, str]] = []
        with entries:
            for entry in entries:
                self._entries += 1
                if self._entries > self._max_entries:
                    self._stop = "entries"
                    break
                if not self._entries & 0xFFF and time.monotonic() > self._deadline:
                    self._stop = "budget"
                    break
                try:
                    if entry.is_symlink():
                        continue
                    info = entry.stat(follow_symlinks=False)
                    attrs = getattr(info, "st_file_attributes", 0)
                    if entry.is_dir(follow_symlinks=False):
                        if attrs & _REPARSE:
                            continue
                        child = entry.path
                        if len(child) > self._max_path:
                            denied += 1
                            continue
                        below = depth + 1
                        if below <= self._never_depth and _is_under_never_touch(child, self._never):
                            continue
                        if below <= self._walked_depth and _norm(child) in self._walked:
                            continue
                        kids.append(child)
                    else:
                        if attrs & _NOT_ON_DISK:
                            continue
                        own += info.st_size
                        files += 1
                        if info.st_size >= self._big_file_min:
                            big.append((info.st_size, entry.path))
                except OSError:
                    denied += 1
        with self._lock:
            if own:
                self._own[key] = self._own.get(key, 0) + own
            self._counted += own
            self._files += files
            self._dirs += 1
            self._denied += denied
            for item in big:
                if len(self._biggest) < BIGGEST_FILES_KEPT:
                    heapq.heappush(self._biggest, item)
                else:
                    heapq.heappushpop(self._biggest, item)
        if not self._stop:
            below = depth + 1
            for child in kids:
                stack.append((child, child if below <= self._max_depth else key, below))


def _known_big_places(root: str) -> list[str]:
    """
    Where the space usually goes on a machine like this one, walked first.
    Each is a place a person never browses, so a plain folder scan misses it:
    model and tool caches, the package caches, the virtual disks of Docker and
    WSL (a .vhdx can be tens of gigabytes in one file), Store apps' data and
    ProgramData. Not Temp: on this machine it took 76 s to walk (20,378 folders
    left by test runs) and the main walk reaches it anyway. Only the ones that
    exist, on this drive.
    """
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    programdata = Path(os.environ.get("ProgramData") or "C:/ProgramData")
    # The HuggingFace cache is under .cache by default. A relocated one (HF_HOME) is
    # not read from the environment here: tests/test_env_documentation.py wants every
    # variable the code reads documented, it is not Jalen's variable, and the main
    # walk finds a relocated cache on this drive anyway - only later.
    candidates = [
        home / ".cache" / "huggingface",
        home / ".cache",                 # torch, pip and others' caches on a developer machine
        local / "pip",
        local / "npm-cache",
        roaming / "npm-cache",
        local / "Docker",
        local / "wsl",                   # WSL2 distros, the newer layout
        local / "Packages",              # Store apps, and older WSL distros (ext4.vhdx)
        home / ".ollama",
        programdata,
    ]
    return [str(p) for p in candidates if p.is_dir()]


def _scan_target(root: str) -> str:
    """The folder a scan of `root` really walks. The drive root, always - the one
    seam the test suite replaces (tests/conftest.py), so no test walks a real drive."""
    return root


def _make_scan(root: str) -> DriveScan:
    target = _scan_target(root)
    return DriveScan(target, never_dirs=_never_touch_dirs(), first=_known_big_places(target))


class _Slot:
    """What is known about one drive: the scan being served, and a re-scan in flight."""

    __slots__ = ("served", "fresh")

    def __init__(self) -> None:
        self.served: Any = None
        self.fresh: Any = None


_slots: dict[str, _Slot] = {}
_slots_lock = threading.Lock()


def _forget_scans() -> None:
    with _slots_lock:
        _slots.clear()


def _system_root() -> str:
    return (os.environ.get("SystemDrive") or "C:").rstrip("\\/") + "\\"


def _start_scan(root: str):
    scan = _make_scan(root)
    scan.start()
    return scan


def _scan_for(root: str):
    """
    The scan to answer from, starting one if there is none and a new one if the
    last finished more than DEEP_SCAN_TTL_S ago. The old result keeps being served
    until the new one has finished, so a re-check never makes an answer worse.
    Returns (scan, a re-scan is running).
    """
    now = time.monotonic()
    with _slots_lock:
        slot = _slots.setdefault(_norm(root), _Slot())
        if slot.fresh is not None and slot.fresh.done.is_set():
            if slot.fresh.snapshot().state != "failed" or slot.served is None:
                slot.served = slot.fresh
            slot.fresh = None
        if slot.served is None:
            slot.served = _start_scan(root)
        elif slot.served.done.is_set() and slot.fresh is None:
            stale = now - (slot.served.finished_at or now) > DEEP_SCAN_TTL_S
            if stale or slot.served.snapshot().state == "failed":
                slot.fresh = _start_scan(root)
        return slot.served, slot.fresh is not None


def _rescan(root: str) -> None:
    """Start a new count unless one is already running (refresh_system_scan)."""
    with _slots_lock:
        slot = _slots.setdefault(_norm(root), _Slot())
        running = [s for s in (slot.served, slot.fresh) if s is not None and not s.done.is_set()]
        if running:
            return
        if slot.served is None:
            slot.served = _start_scan(root)
        else:
            slot.fresh = _start_scan(root)


# ------------------------------------------------------- choosing the places
def _depth_below(key: str, root: str) -> int:
    return key.rstrip("\\/").count(os.sep) - root.rstrip("\\/").count(os.sep)


def pick_places(own: dict, root: str, n: int = TOP_PLACES, system_bytes: int = 0) -> list:
    """
    The n biggest places, as specific as is honest.

    Start from the folders at the top of the drive and keep replacing the
    biggest one by what is inside it, for as long as one part of it holds at
    least SPLIT_SHARE of it: "Users, 100 GB" says nothing and "Chrome, 18 GB"
    does. A folder whose size is spread over many things stays whole. Files
    sitting loose in a folder that also has subfolders count as an entry of
    their own. `system_bytes` is the part of the drive the scan may not open;
    it competes for a place too, when it is big enough to be worth naming.
    """
    root = os.path.abspath(root)
    inclusive: dict[str, int] = dict(own)
    children: dict[str, list[str]] = defaultdict(list)
    by_depth: dict[int, list[str]] = defaultdict(list)
    for key in own:
        by_depth[_depth_below(key, root)].append(key)
    for depth in range(max(by_depth, default=0), 0, -1):
        for key in by_depth.get(depth, ()):
            parent = os.path.dirname(key)
            if parent not in inclusive:
                inclusive[parent] = 0
                by_depth[depth - 1].append(parent)
            inclusive[parent] += inclusive[key]
            children[parent].append(key)

    def inside(node: Place) -> list:
        found = [Place(child, inclusive[child]) for child in children.get(node.path, ())]
        loose = own.get(node.path, 0)
        if found and loose > 0:
            found.append(Place(node.path, loose, loose=True))
        return found

    frontier = inside(Place(root, inclusive.get(root, 0)))
    if not frontier and inclusive.get(root, 0) > 0:
        frontier = [Place(root, inclusive[root], loose=True)]
    if system_bytes >= MIN_SYSTEM_REPORT_BYTES:
        frontier.append(Place(SYSTEM_PATH, int(system_bytes)))

    while True:
        frontier.sort(key=lambda place: place.size, reverse=True)
        for index, node in enumerate(frontier[:n]):
            if node.loose or node.path == SYSTEM_PATH:
                continue
            parts = inside(node)
            if parts and max(part.size for part in parts) >= SPLIT_SHARE * node.size:
                frontier[index:index + 1] = parts
                break
        else:
            break
    return [place for place in frontier[:n] if place.size > 0]


_protected = None


def _protected_engine():
    """The SafetyEngine every "is this file protected?" question goes to, built once."""
    global _protected
    if _protected is None:
        from ..config import CONFIG
        from ..safety import SafetyEngine

        _protected = SafetyEngine(CONFIG)
    return _protected


# What he would call a place, by where it is under his home folder (lower case).
_HOME_NAMES = {
    (".cache", "huggingface"): "the HuggingFace model cache",
    (".cache",): "your .cache folder",
    ("appdata",): "your AppData folder",
    ("appdata", "local"): "AppData Local",
    ("appdata", "roaming"): "AppData Roaming",
    ("appdata", "local", "temp"): "your Temp folder",
    ("appdata", "local", "pip"): "pip's download cache",
    ("appdata", "local", "npm-cache"): "npm's cache",
    ("appdata", "roaming", "npm-cache"): "npm's cache",
    ("appdata", "local", "docker"): "Docker's data",
    ("appdata", "local", "packages"): "Windows Store app data",
}
_ROOT_NAMES = {"$recycle.bin": "the Recycle Bin", "programdata": "ProgramData"}


def _name_of(path: str, root: str, home: str) -> str:
    """A single folder named as he would say it, without its parents."""
    try:
        below_home = tuple(part.lower() for part in Path(path).relative_to(home).parts)
    except ValueError:
        below_home = None
    if below_home == ():
        return "your user folder"
    if below_home is not None and below_home in _HOME_NAMES:
        return _HOME_NAMES[below_home]
    if below_home is not None and len(below_home) == 1:
        return f"your {Path(path).name} folder"
    if below_home is not None and len(below_home) == 4 and below_home[:3] == ("appdata", "local", "packages"):
        # A Store app's folder is "<Name>_<publisher id>": the id is noise out loud.
        return f"{Path(path).name.split('_')[0]} app data"
    leaf = Path(path).name or path
    try:
        if len(Path(path).relative_to(root).parts) == 1 and leaf.lower() in _ROOT_NAMES:
            return _ROOT_NAMES[leaf.lower()]
    except ValueError:
        pass
    return leaf


def place_label(place: Place, root: str, home: str, biggest: list) -> str:
    """How a Place is said aloud. A folder that is mostly one big file is named by
    the file (unless that file is on the never-touch list)."""
    if place.path == SYSTEM_PATH:
        return "Windows and other system files I don't look inside"
    drive = f"{root[:1].upper()} drive" if root[:2].endswith(":") else "the drive"
    if place.loose and _norm(place.path) == _norm(root):
        return f"files sitting loose at the top of your {drive}"
    here = _norm(place.path)
    for size, file_path in biggest:
        if _norm(os.path.dirname(file_path)) == here and size * 2 >= place.size:
            try:
                if _protected_engine().protected_path(file_path):
                    break
            except Exception:
                break
            return f"the file {os.path.basename(file_path)} in the {Path(place.path).name} folder"
    name = _name_of(place.path, root, home)
    if place.loose:
        return f"the files directly in {name}"
    try:
        depth = len(Path(place.path).relative_to(root).parts)
    except ValueError:
        depth = 1
    if depth >= 2 and name == Path(place.path).name:
        # No name he would know it by: say which folder it is in, because
        # "Cache" or "data" alone could be anywhere.
        return f"{name} in {_name_of(os.path.dirname(place.path), root, home)}"
    return name


def describe_scan(snapshot: Snapshot, used: int, home: str) -> str | None:
    """
    What a scan, finished or not, can truthfully say about a drive.

    None when the scan failed (there is nothing to say; the caller falls back).
    Never an answer that is only "still scanning": a scan in progress says how
    much it has counted of what is in use and names the places found so far.
    """
    if snapshot.state == "failed":
        return None
    root = snapshot.root
    drive = f"{root[:1].upper()} drive" if root[:2].endswith(":") else "drive"
    done = snapshot.state == "done"
    system = max(0, used - snapshot.counted) if done else 0
    places = pick_places(snapshot.own, root, TOP_PLACES, system_bytes=system)
    spoken = [
        (f"{place_label(place, root, home, snapshot.biggest)} at "
         f"{'about ' if place.path == SYSTEM_PATH else ''}{_size_str(place.size)}")
        for place in places
    ]
    listing = (", ".join(spoken[:-1]) + (" and " if len(spoken) > 1 else "") + spoken[-1]) if spoken else ""
    promise = " I'm only reporting sizes; nothing gets deleted unless you tell me to."
    covered = f"{_size_str(snapshot.counted)} of the {_size_str(used)} in use"

    if snapshot.state == "running":
        if not places:
            return (f"I've only just started counting your {drive}, so I have nothing to list yet. "
                    f"When you ask again in a minute or two I'll have the first figures.{promise}")
        return (f"I'm still counting your {drive}, so this isn't final. So far I've counted "
                f"{covered}. The biggest places so far: {listing}. When you ask again in a few "
                f"minutes I'll have the whole picture.{promise}")
    if not done:
        reason = (f"after {snapshot.files:,} files" if snapshot.state == "entries"
                  else f"after about {max(1, round(snapshot.elapsed_s / 60))} minutes")
        found = f" The biggest places I found: {listing}." if listing else ""
        return (f"I stopped counting your {drive} {reason}, so there's more beyond this: I covered "
                f"{covered}.{found}{promise}")
    if not listing:
        return f"I didn't find anything big on your {drive}.{promise}"
    text = f"On your {drive}, the biggest places are: {listing}."
    if snapshot.biggest:
        size, file_path = snapshot.biggest[0]
        name = os.path.basename(file_path)
        try:
            protected = bool(_protected_engine().protected_path(file_path))
        except Exception:
            protected = True
        if not protected and not any(name in said for said in spoken):
            text += f" The single biggest file is {name} at {_size_str(size)}."
    return text + promise


def _pick_drive(drives: list, asked: str) -> tuple:
    """(the drive, its letter): the one he asked about by letter ("D", "d drive", "D:"),
    else the one Windows is on. The drive is None when there is no such drive."""
    tokens = (asked or "").replace(":", " ").replace("\\", " ").replace("/", " ").split()
    letter = next((t for t in tokens if len(t) == 1 and t.isalpha()), "")
    letter = (letter or _system_root()[0]).upper()
    return next((d for d in drives if str(d.get("mountpoint", ""))[:1].upper() == letter), None), letter


def _wait_for(scan) -> None:
    """Give a scan that is still going the chance to finish its known places, no more."""
    if scan.done.is_set():
        return
    scan.quick_done.wait(timeout=ASK_WAIT_S)


# ------------------------------------------------------------------ cleanup
def _dir_total_size(
    path: Path,
    never_dirs: list[str],
    max_entries: int = MAX_CATEGORY_ENTRIES,
    time_budget_s: float = CATEGORY_TIME_BUDGET_S,
) -> tuple[int, int, bool]:
    """Total bytes + file count under `path`, bounded. (0, 0, False) if the
    path doesn't exist or is itself protected."""
    if not path.is_dir() or _is_under_never_touch(path, never_dirs) or _is_a_link(path):
        return 0, 0, False
    total = 0
    count = 0
    capped = False
    deadline = time.monotonic() + time_budget_s
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [
            d for d in dirnames
            if not _is_under_never_touch(Path(dirpath) / d, never_dirs)
            and not _is_a_link(Path(dirpath) / d)
        ]
        for name in filenames:
            count += 1
            if count > max_entries or time.monotonic() > deadline:
                capped = True
                break
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
        if capped:
            break
    return total, count, capped


def _old_downloads_size(
    days: int,
    never_dirs: list[str],
    max_entries: int = MAX_CATEGORY_ENTRIES,
    time_budget_s: float = CATEGORY_TIME_BUDGET_S,
) -> tuple[int, int, bool]:
    downloads = Path.home() / "Downloads"
    if not downloads.is_dir() or _is_a_link(downloads):
        return 0, 0, False
    cutoff = time.time() - days * 86400
    total = 0
    count = 0
    scanned = 0
    capped = False
    deadline = time.monotonic() + time_budget_s
    for dirpath, dirnames, filenames in os.walk(downloads):
        dirnames[:] = [
            d for d in dirnames
            if not _is_under_never_touch(Path(dirpath) / d, never_dirs)
            and not _is_a_link(Path(dirpath) / d)
        ]
        for name in filenames:
            scanned += 1
            if scanned > max_entries or time.monotonic() > deadline:
                capped = True
                break
            fp = Path(dirpath) / name
            try:
                st = fp.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                total += st.st_size
                count += 1
        if capped:
            break
    return total, count, capped


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


def _recycle_bin_total() -> tuple[int, int] | None:
    """Summed recycle-bin size/count across every fixed drive, via the
    same Shell API Explorer's own recycle bin properties dialog uses.
    None if it can't be read at all (not Windows, or the call itself fails);
    (0, 0) is a legitimate "empty" answer and is kept distinct from that."""
    if not IS_WINDOWS:
        return None
    try:
        import psutil
    except ImportError:
        return None

    total_size = 0
    total_items = 0
    found_any = False
    for part in psutil.disk_partitions(all=False):
        opts = (part.opts or "").lower()
        if "cdrom" in opts or not part.fstype:
            continue
        info = _SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
        try:
            hr = ctypes.windll.shell32.SHQueryRecycleBinW(part.mountpoint, ctypes.byref(info))
        except OSError:
            continue
        if hr == 0:  # S_OK
            found_any = True
            total_size += info.i64Size
            total_items += info.i64NumItems
    return (total_size, total_items) if found_any else None


def _local_appdata() -> Path | None:
    val = os.environ.get("LOCALAPPDATA")
    return Path(val) if val else None


def _browser_cache_paths() -> list[tuple[str, Path]]:
    local = _local_appdata()
    if local is None:
        return []
    candidates = [
        ("Chrome", local / "Google" / "Chrome" / "User Data" / "Default" / "Cache"),
        ("Edge", local / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache"),
    ]
    profiles = local / "Mozilla" / "Firefox" / "Profiles"
    if profiles.is_dir():
        try:
            for prof in profiles.iterdir():
                cache2 = prof / "cache2"
                if cache2.is_dir():
                    candidates.append(("Firefox", cache2))
        except OSError:
            pass
    return [(name, p) for name, p in candidates if p.is_dir()]


def _cleanup_uncached(downloads_older_than_days: int = 30) -> str:
    """
    Specifically safe-to-delete things, with real measured sizes — GREEN,
    read-only. This function only suggests; it never deletes anything.
    Deleting a file goes through the existing delete_file tool, which is RED
    and asks first — that boundary is deliberate and isn't crossed here.
    """
    never_dirs = _never_touch_dirs()
    lines: list[str] = []
    recoverable = 0

    temp_env = os.environ.get("TEMP") or os.environ.get("TMP")
    temp_dir = Path(temp_env) if temp_env else Path.home() / "AppData" / "Local" / "Temp"
    t_size, t_count, t_capped = _dir_total_size(temp_dir, never_dirs)
    if t_count:
        cap_note = " (more beyond this — scan capped)" if t_capped else ""
        lines.append(f"Temp files: {_size_str(t_size)} across {t_count:,} files{cap_note}.")
        recoverable += t_size

    d_size, d_count, d_capped = _old_downloads_size(downloads_older_than_days, never_dirs)
    if d_count:
        cap_note = " (more beyond this — scan capped)" if d_capped else ""
        lines.append(
            f"Downloads older than {downloads_older_than_days} days: "
            f"{_size_str(d_size)} across {d_count:,} files{cap_note}."
        )
        recoverable += d_size

    rb = _recycle_bin_total()
    if rb is not None:
        rb_size, rb_items = rb
        if rb_items:
            lines.append(f"Recycle bin: {_size_str(rb_size)} across {rb_items:,} items.")
            recoverable += rb_size
        else:
            lines.append("Recycle bin is already empty.")

    cache_bits = []
    for name, path in _browser_cache_paths():
        c_size, c_count, _ = _dir_total_size(path, never_dirs)
        if c_count:
            cache_bits.append(f"{name} at {_size_str(c_size)}")
            recoverable += c_size
    if cache_bits:
        lines.append("Browser caches: " + ", ".join(cache_bits) + ".")

    local = _local_appdata()
    if local is not None:
        pip_size, pip_count, _ = _dir_total_size(local / "pip" / "Cache", never_dirs)
        if pip_count:
            lines.append(f"Pip cache: {_size_str(pip_size)}.")
            recoverable += pip_size

        npm_size, npm_count, _ = _dir_total_size(local / "npm-cache", never_dirs)
        if npm_count:
            lines.append(f"NPM cache: {_size_str(npm_size)}.")
            recoverable += npm_size

    if not lines:
        return "I didn't find anything obviously safe to clean up right now."

    body = " ".join(lines)
    footer = (
        " Windows also collects old update files under C:\\Windows that I don't scan since "
        "that folder is off-limits — Disk Cleanup handles those. I'm only reporting sizes here; "
        "nothing gets deleted unless you tell me to."
    )
    return f"You could likely free up around {_size_str(recoverable)} total. {body}{footer}"


# ----------------------------------------------------------------- memory
def memory_report(top_n: int = 5) -> str:
    """Overall RAM usage plus the biggest consumers — GREEN, read-only."""
    try:
        import psutil
    except ImportError:
        return "psutil isn't installed, so I can't read memory usage."

    vm = psutil.virtual_memory()

    # Aggregated by process NAME, not by individual PID: apps like Chrome or
    # svchost.exe routinely run dozens of processes, and listing each one
    # separately buries the actual signal ("chrome.exe: 210MB" x30 tells you
    # nothing "Chrome: 6.3 gigabytes" doesn't say better).
    by_name: dict[str, int] = {}
    for proc in psutil.process_iter(["name", "memory_info"]):
        try:
            info = proc.info
            mem = info.get("memory_info")
            if mem is None:
                continue
            name = info.get("name") or "unknown"
            by_name[name] = by_name.get(name, 0) + mem.rss
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    top = sorted(by_name.items(), key=lambda kv: kv[1], reverse=True)[:top_n]

    summary = (
        f"You're using {vm.percent:.0f} percent of your memory — "
        f"{_size_str(vm.used)} out of {_size_str(vm.total)}, with {_size_str(vm.available)} available."
    )
    if top:
        items = ", ".join(f"{name} at {_size_str(rss)}" for name, rss in top)
        summary += f" The biggest users are: {items}."
    return summary


# --------------------------------------------------------------------- dispatch

# --------------------------------------------------- cached public entry points
def disk_report(drive: str = "") -> str:
    """
    What is filling a drive - GREEN, read-only. Free space on every drive, then the
    five biggest places on this one (the drive Windows is on unless he names another).

    The count is a background walk of the whole drive (DriveScan). A finished one
    is answered at once, with its age when it is old and a re-count started behind
    it. One still running is waited for only while it covers the known big places,
    then answered with what it has, said to be partial - never "still scanning"
    and nothing else - and it carries on, so the next ask has the finished answer.
    """
    try:
        import psutil  # noqa: F401
    except ImportError:
        return "psutil isn't installed, so I can't read disk usage."

    drives = _drive_usage()
    if not drives:
        return "I couldn't read any drive usage on this machine."

    drive_bits = [
        f"your {d['label']} has {_size_str(d['free'])} free out of {_size_str(d['total'])}, "
        f"{d['percent']:.0f} percent full"
        for d in drives
    ]
    line = "; ".join(drive_bits)
    summary = line[:1].upper() + line[1:] + "."

    target, letter = _pick_drive(drives, drive)
    if target is None:
        return summary + f" I can't see a {letter} drive."

    scan, rechecking = _scan_for(str(target["mountpoint"]))
    _wait_for(scan)
    used = max(0, int(target["total"]) - int(target["free"]))
    said = describe_scan(scan.snapshot(), used, str(Path.home()))
    if said is None:
        return summary + _user_folder_summary()

    note = ""
    if scan.done.is_set() and scan.finished_at is not None:
        note = _age_note(time.monotonic() - scan.finished_at)
    if rechecking and note:
        note += " I'm counting again now, so ask again in a few minutes for current figures."
    return f"{summary} {said}{note}"


def cleanup_suggestions(downloads_older_than_days: int = 30) -> str:
    """
    What's safe to delete, with real sizes — GREEN. Suggests only; nothing
    in this module deletes anything (deletion goes through RED delete_file).
    """
    if downloads_older_than_days != 30:
        return _cleanup_uncached(downloads_older_than_days)  # non-default: measure for real
    return _cached("cleanup", _cleanup_uncached)

REGISTRY: dict[str, Any] = {
    "disk_report": disk_report,
    "cleanup_suggestions": cleanup_suggestions,
    "memory_report": memory_report,
    "refresh_system_scan": refresh_system_scan,
}




def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
