r"""
Moving a whole folder to another drive, and not losing it on the way.

WHAT WAS MISSING
----------------
His C: drive has been at 99-100 percent for weeks (105 MB free on
2026-08-22, 8.4 GB of 157 GB today) while D: has 296 GB free. On 2026-08-22
08:12 he said "...move my Cafe folder and Echo Pulse folder and..." - the
sentence was cut off after "and" and Jalen answered with a question - and
there was nothing to do it with. What existed was move_file - GREEN, so it
asks nothing - which hands a directory to shutil.move. Across drives that is
copytree followed by rmtree: no count, no comparison, no refusal of
C:/Windows or a Program Files folder, no scan for the never-touch list, and it
runs INSIDE the turn, so he hears nothing for as long as it takes.

THE SHAPE OF THE FIX
--------------------
Three tools, because "do it" and "tell me what it would do" are different
requests and the second has to come first:

    plan_folder_move    GREEN  the dry run: "4.2 GB, 1,800 files, frees 4.2 GB
                               on C". Changes nothing.
    move_folder         RED    does it, as a background job.
    folder_move_status  GREEN  where a move is, and whether one was cut short.

move_folder refuses to start until plan_folder_move has been run for the same
folder and destination in the last PLAN_TTL_S - the numbers have to have been
spoken before the question "Confirm?" is asked, and the brain is only TOLD to
do that; this makes it so. The question itself names the real source, the real
(possibly redirected) destination, whether a link is left, and every protected
file the folder carries with it (confirmation_summary).

WHY RED
-------
The last step permanently deletes the original. It cannot go to the Recycle
Bin: the bin lives on the same nearly-full drive, so a folder "recycled" there
frees nothing, which is the whole point. Everything before that step is
reversible and everything after it is not, and a verification with a blind
spot (a file written while it was being checked, a sample that missed the one
bad block) turns into lost work at exactly that step. That is F46's
definition of RED. It is also long, heavy on the disk, and moves where a path
lives, which can break a program he did not think about.

THE COPY IS BUILT BESIDE THE DESTINATION, NOT IN IT
---------------------------------------------------
The first version copied straight into the destination and resumed into
whatever was already there. An independent review reproduced what that meant:
a record left by an earlier failed try never expired, so the next run treated
a destination he had since filled with his own files as "the half-done copy of
this move" and replaced his same-named files with the source's, and the
leftovers it could not tell from its own were never cleaned up. Now every byte
is written into <final>.jalen-incoming-<key> (STAGING_MARK), a folder that
exists only because this module made it, and is moved onto <final> in ONE
rename at the very end, after the copy has been checked and only if <final> is
absent or empty. So:

  * nothing of his is ever overwritten - nothing of his is ever in the folder
    the copy is written into;
  * a stale record cannot authorise anything, because the record decides
    nothing: what a resumed run may do is read from the disk (is there a
    staging folder with this key? a renamed original with this key?);
  * everything in the staging folder is ours, so a repair may delete anything
    in it - a file the source no longer has (a Chrome .crdownload renamed
    to .pdf after it was copied) included. _repair refuses to touch a folder
    whose name does not carry STAGING_MARK.

ORDER OF OPERATIONS, AND WHY
----------------------------
    1  copy      to the staging folder, file by file, to <name>.jalenpart then
                 renamed, so a half-written file never looks finished. Skips
                 files already there with the same size and modified time -
                 that is what makes it resumable, and why there is no separate
                 "resume".
    2  verify    every file's size and modified time, every folder, and a
                 SHA-256 of EVERY file up to FULL_HASH_UP_TO_BYTES (a sample
                 above that). Anything wrong is repaired and checked again, up
                 to MAX_VERIFY_PASSES; then it STOPS and the original is
                 untouched.
    3  rename    the original to <name>.jalen-moved-<key>. Same volume, so it is
                 instant - and it is a test: Windows will not rename a folder
                 that a program has files open in, so "something is using it" is
                 learned here, BEFORE anything is deleted.
    4  re-verify the renamed original against the staging copy (metadata
                 only). A download that landed between steps 2 and 3 is caught
                 here and copied.
    5  place     the staging folder is renamed onto <final>. Refused, and the
                 original put back, if <final> is occupied.
    6  check     everything that is left of the renamed original is on the new
                 drive, same size and date. This is the check that makes the
                 delete safe at ANY point, including after a cut: a half-
                 deleted original is still a subset of the copy.
    7  link      a junction at the old path pointing at the new one.
    8  delete    the renamed original.

A kill anywhere leaves a state the next run recognises from the names on disk,
and saying the same move again (plan_folder_move) says "finish" rather than
refusing: see _unfinished_moves. What the first version claimed - "nothing was
removed, say it again and I'll pick up" - was false after step 8 began, and
saying it again was refused as "already a link"; both are fixed.

THE JUNCTION (decided, not defaulted)
-------------------------------------
Left by default for a move to ANOTHER drive, because the point is to free C:
while everything that points at the old path keeps working - Chrome's download
folder, Explorer's Downloads/Videos shortcuts, a project's recent-files list,
a .lnk, one of his own aliases. Without it, "move my Downloads to D" frees 4 GB
and then Chrome recreates C:\Users\user\Downloads and fills it again. It is a
junction rather than a symlink because a symlink needs administrator rights or
Developer Mode and a junction needs neither. leave_link=False ("and don't
leave a shortcut") removes the old path entirely. For a move on the SAME drive
nothing is freed and the link has no job, so it is not left unless he asks:
"move Cafe into Documents" leaves Cafe in Documents, not Cafe in both places.
The cost of a junction is that walkers that follow it count D: files as C:
usage - see sysinfo._is_a_link.

A DESTINATION THAT IS TAKEN
---------------------------
Found by running the dry run on the real machine 2026-10-01: D: already holds
a D:\Downloads (2024), so "move my Downloads to D" was refused with "pick
another place" - a dead end. When he names a DRIVE and the folder's name is
taken there, the move goes to "Downloads from C" (then "... 2") beside it and
he is told before anything is asked. It is never merged: two different
report.pdf have no right answer, and verification could not tell his files from
mine. When he names the exact folder, a taken place is still a refusal.

WHAT IT REFUSES
---------------
Windows, Program Files, ProgramData, AppData (installed apps), the profile
root, a drive root, Jalen's own folder and the Python it runs on, a folder a
running program is started from, a OneDrive folder (moving it out deletes it
from the cloud too), anything on the never-touch list, and a folder with
links or cloud-only placeholders inside it. The never-touch handling:

  * a never-touch PATH inside the folder refuses the move - the list is by
    location, so moving the folder would silently stop protecting it;
  * a never-touch NAME PATTERN (.env, *.pem) refuses it too, EXCEPT inside
    node_modules / .venv / site-packages / __pycache__. 61 of 29,698 files in
    a real site-packages (0.2%) match - Secret.py, cacert.pem,
    encrypted_credentials.py - and they are third-party code, so refusing
    every project with a venv would make "move my projects folder" impossible.

PROTECTED FILES HE HAS SAID MAY COME ALONG
------------------------------------------
His real projects (Cafe, SAT TOP, the folders he asked for in August) all hold
a .env or a token file, so "a protected name anywhere inside refuses the move"
made the very thing he asked for impossible. Decided by him: a folder that
holds .env files MAY be moved, after a named yes. Exactly this, and no wider:

  * only the names in CARRYABLE_PATTERNS (.env, .env.*, *.env, *token*.json),
    and only when no other never-touch pattern matches the same name - a
    my_secret.env or a client_secret.json is still refused, as is every
    key, certificate, vault, session, password and credential file;
  * only FILES: a folder whose name is protected is still refused;
  * each one is listed by its relative path in the dry run AND in the
    confirmation question ("this folder has 2 protected files that will be
    carried over unread: .env, config/token.json"), and the job carries only
    what he was told about - a protected file that turns up later stops the
    move before anything is copied (or before anything is removed, if it
    turned up during the copy);
  * they are copied byte for byte and compared byte for byte, in memory, one
    chunk at a time. Their CONTENTS are never hashed into a log, never put in a
    record, a status line, a spoken sentence, an error or the audit trail -
    only their paths are;
  * config/safety.yaml is not touched. Every other tool still refuses these
    files; the exception lives in this module and is pinned by
    tests/test_folder_move_review.py.
"""
from __future__ import annotations

import fnmatch
import hashlib
import heapq
import json
import os
import random
import re
import shutil
import stat
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent.parent
MOVES_DIR = ROOT / "data" / "folder_moves"

# ---------------------------------------------------------------- constants
# How long the dry run may spend counting, in a voice turn where nothing else
# is spoken meanwhile. launcher.py measured 9 s of silence reading as "it's
# not responding", so the ceiling is 8 s. Measured 2026-10-01 on this machine:
# a COLD walk of a real site-packages (29,698 files) with a stat per file ran
# at 4,345 files/s, a warm walk of 20,302 files at 154,306 files/s. 8 s is
# ~35,000 files cold, which covers a Downloads or a project folder; past it the
# plan says "at least". It is only a CEILING ON THE ESTIMATE: the job scans
# with no budget before it copies anything, and refuses then if it finds
# something the dry run never reached.
PLAN_TIME_BUDGET_S = 8.0

# How long a spoken dry run stays good enough to start the move it described.
# NOT MEASURED: a quarter of an hour is long enough to hear it, think, and say
# yes, and short enough that the folder is still what he was told about.
PLAN_TTL_S = 900.0

# What is left free on the destination. NOT MEASURED: a floor, not a tuning -
# an NTFS volume with nothing free cannot write its own journal.
HEADROOM_BYTES = 1 << 30

# Every file is compared by SHA-256 on both sides up to this many bytes in the
# folder. Measured 2026-10-01 on this machine: SHA-256 runs at 270 MB/s warm
# (an earlier run here measured 806 MB/s; it depends on what else is running).
# Both sides are read, so 8 GiB is about a minute warm. Cold, on the spinning
# disk this machine has, 100 MB/s is the number to plan for, which makes it
# about three minutes - NOT MEASURED cold. Next to a copy that takes about as
# long again, and in a background job, that is cheap for what it buys: the
# delete that follows cannot be undone, and size plus modified time cannot
# see a block that was written wrong. Above this the sample below is used, and
# the spoken result says how many files were hashed rather than implying all.
FULL_HASH_UP_TO_BYTES = 8 * 1024 ** 3

# The hash sample, for folders above FULL_HASH_UP_TO_BYTES: 100 random files
# plus the 5 largest, at most 512 MiB read on each side. A file bigger than
# what is left of the budget is checked by size and modified time only.
SAMPLE_FILES = 100
SAMPLE_LARGEST = 5
SAMPLE_BYTE_BUDGET = 512 * 1024 * 1024

# Two modified times closer than this are the same. Measured: shutil.copy2 on
# NTFS->NTFS keeps mtime exact (0 ns difference over a 64 MB file). 2 s is
# FAT/exFAT's own granularity (documented, NOT MEASURED here), so a copy onto
# one of those is not called different for rounding.
MTIME_TOLERANCE_NS = 2_000_000_000

# Tries per file, and the pause between them. NOT MEASURED: a file another
# program has open usually frees up in a moment or not at all.
COPY_RETRIES = 2
RETRY_PAUSE_S = 1.0

# Verify, repair, verify. Three passes: one for the ordinary case (a file
# changed during the copy), one for the unlucky case, one to prove it.
MAX_VERIFY_PASSES = 3

# Progress is written to the record at most this often.
PROGRESS_EVERY_S = 1.0

# A record that is "queued" and has not been picked up by a process in this
# long never will be: the child starts in about 2 s once imported. NOT MEASURED.
STALE_QUEUE_S = 120.0

# A background job's deadline. devwork's default is an hour. Measured here
# 2026-10-01 on a synthetic 20,302-file, 98 MB tree with Defender on and every
# file new to it: 82 files/s cold (246 s), 430 files/s warm; the loop's own
# overhead over a bare copyfile is ~0.8 ms a file, so the OS and the antivirus
# set the pace, not this module. A 100,000-file project is ~20 minutes cold
# and a 100 GB video folder at a spinning disk's 30 MB/s is ~55 minutes - the
# first fits in an hour, the second does not. Twelve hours; a cut job resumes.
JOB_TIMEOUT_S = 12 * 3600.0

# At most this many problem names go into a spoken sentence.
SPOKEN_EXAMPLES = 3

# At most this many protected files are read out one by one, and more than
# MAX_CARRIED of them is a folder that is refused: "yes" to twenty-one names
# is not a named yes. NOT MEASURED: his real Cafe and SAT TOP hold one or two.
SPOKEN_CARRIED = 8
MAX_CARRIED = 20

PART_SUFFIX = ".jalenpart"
TRASH_MARK = ".jalen-moved-"
STAGING_MARK = ".jalen-incoming-"

# Directory names whose files are third-party code, not his secrets. See the
# module docstring for the measurement.
DEPENDENCY_DIRS = frozenset({"node_modules", ".venv", "venv", "site-packages", "__pycache__"})

# The protected NAME PATTERNS (as they are spelled in config/safety.yaml's
# never_touch.patterns, case-folded) whose files a folder may carry along once
# he has been told each one and said yes. See the module docstring: his
# decision named .env files; "config/token.json" was his own example of the
# other. A name that also matches any pattern NOT in this set is refused.
CARRYABLE_PATTERNS = frozenset({".env", ".env*", "*.env", "*token*.json"})

# Windows file attributes (st_file_attributes).
_REPARSE_POINT = 0x400
_OFFLINE = 0x1000
_RECALL_ON_OPEN = 0x40000
_RECALL_ON_DATA_ACCESS = 0x400000
_PLACEHOLDER_BITS = _OFFLINE | _RECALL_ON_OPEN | _RECALL_ON_DATA_ACCESS

# Above this a path is given to Windows with the \\?\ prefix. MAX_PATH is 260
# and a folder is limited to 248; the margin covers the ".jalenpart" and the
# ".jalen-moved-<key>" / ".jalen-incoming-<key>" this module appends.
# node_modules trees exceed 260.
_LONG_PATH_AT = 200

# Names at the top of a drive that are the system's, not his.
_DRIVE_ROOT_SYSTEM_NAMES = frozenset({
    "$recycle.bin", "system volume information", "recovery", "boot", "perflogs",
    "config.msi", "windows.old", "$windows.~bt", "msocache", "$sysreset",
})

_DRIVE_WORDS = re.compile(
    r"^(?:the |my )?(?:(?:local )?(?:drive|disk) ([c-hj-z])|([c-hj-z])(?:\s*:)?(?:\s+(?:drive|disk))?)\s*[\\/]?$",
    re.I)
_LOOKS_LIKE_A_PATH = re.compile(r"^(?:[a-zA-Z]:[\\/]|~|%|\\\\|\.{1,2}[\\/]|/)")
_KNOWN_FOLDERS = {
    "downloads": "Downloads", "download": "Downloads",
    "documents": "Documents", "docs": "Documents",
    "desktop": "Desktop",
    "pictures": "Pictures", "photos": "Pictures",
    "videos": "Videos", "video": "Videos", "movies": "Videos",
    "music": "Music",
}


def is_known_folder_name(text: str) -> bool:
    """'downloads', 'my videos', 'the pictures folder': a folder every Windows profile has, said by name."""
    name = re.sub(r"^(?:my|the)\s+", "", (text or "").strip(), flags=re.I)
    name = re.sub(r"\s+(?:folder|directory)$", "", name, flags=re.I).strip().lower()
    return name in _KNOWN_FOLDERS


def _say(text: str) -> None:
    """A line for the log. Never raises: a closed stdout must not cost the move."""
    try:
        print(text, flush=True)
    except (OSError, ValueError, UnicodeError):
        pass


# ------------------------------------------------------------------- paths
def _lp(path: Any) -> str:
    r"""
    The string to hand Windows for this path: plain while it is short, with the
    \\?\ prefix once it is long enough that MAX_PATH would refuse it.
    """
    s = os.fspath(path)
    if os.name != "nt" or s.startswith("\\\\?\\"):
        return s
    s = os.path.abspath(s)
    if len(s) < _LONG_PATH_AT:
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def _norm(path: Any) -> str:
    """One comparable spelling: absolute, case-folded, forward slashes, no trailing slash."""
    s = os.path.normcase(os.path.abspath(os.fspath(path))).replace("\\", "/")
    return s.rstrip("/") if len(s) > 3 else s


def _within(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def _related(a: Any, b: Any) -> bool:
    """One is the other, or inside it."""
    na, nb = _norm(a), _norm(b)
    return _within(na, nb) or _within(nb, na)


def _attrs_of(st: os.stat_result) -> int:
    return int(getattr(st, "st_file_attributes", 0) or 0)


def _is_reparse(path: Any) -> bool:
    try:
        st = os.lstat(_lp(path))
    except OSError:
        return False
    return bool(_attrs_of(st) & _REPARSE_POINT) or stat.S_ISLNK(st.st_mode)


def _is_junction(path: Any) -> bool:
    try:
        return os.path.isjunction(_lp(path))
    except (AttributeError, OSError):
        return False


def _nearest_existing(path: Any) -> str:
    p = Path(os.path.abspath(os.fspath(path)))
    while not p.exists() and p.parent != p:
        p = p.parent
    return str(p)


def _same_volume(a: Any, b: Any) -> bool:
    try:
        return os.stat(_nearest_existing(a)).st_dev == os.stat(_nearest_existing(b)).st_dev
    except OSError:
        return False


def _free_bytes(path: Any) -> int:
    return shutil.disk_usage(_nearest_existing(path)).free


def _is_fixed_drive(path: Any) -> bool:
    """A drive that stays: not a USB stick (a junction to one dangles when it is pulled) and not a share."""
    if os.name != "nt":
        return True
    anchor = os.path.splitdrive(os.path.abspath(os.fspath(path)))[0]
    if not anchor or anchor.startswith("\\\\"):
        return False
    try:
        import ctypes

        return ctypes.windll.kernel32.GetDriveTypeW(anchor + "\\") == 3  # DRIVE_FIXED
    except Exception:  # noqa: BLE001 - unknown means not proven fixed
        return False


def _drive_word(path: Any) -> str:
    drive = os.path.splitdrive(os.path.abspath(os.fspath(path)))[0]
    if len(drive) == 2 and drive[1] == ":":
        return f"the {drive[0].upper()} drive"
    return "that drive"


def _say_bytes(n: float) -> str:
    try:
        from .sysinfo import _size_str

        return _size_str(n)
    except Exception:  # noqa: BLE001
        return f"{n / 1e9:.1f} gigabytes" if n >= 1e9 else f"{n / 1e6:.0f} megabytes"


def _count(n: int, noun: str) -> str:
    return f"{n:,} {noun}" + ("" if n == 1 else "s")


def _names(items: list[str]) -> str:
    """'.env, config/token.json' - and '... and 3 more' past SPOKEN_CARRIED. Paths only, never contents."""
    shown = ", ".join(items[:SPOKEN_CARRIED])
    extra = len(items) - SPOKEN_CARRIED
    return shown + (f" and {extra} more" if extra > 0 else "")


# ----------------------------------------------------------- what is refused
def _system_roots() -> list[Path]:
    """
    Folders that are Windows's or an installed program's, and everything
    under them. Read from the environment each call, so a profile that is not
    C:\\Users\\user still gets its own AppData refused.
    """
    roots: list[Path] = []
    for var in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                "ProgramData", "APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(var)
        if value:
            roots.append(Path(value))
    roots.append(Path.home() / "AppData")
    for part in {os.path.splitdrive(str(r))[0] for r in roots} | {os.path.splitdrive(str(Path.home()))[0]}:
        if part:
            for name in _DRIVE_ROOT_SYSTEM_NAMES:
                roots.append(Path(part + "\\") / name)
    seen: set[str] = set()
    unique = []
    for r in roots:
        key = _norm(r)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _cloud_sync_roots() -> list[Path]:
    out = []
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = os.environ.get(var)
        if value:
            out.append(Path(value))
    return out


def _own_interpreter_dirs() -> list[Path]:
    dirs = {sys.prefix, getattr(sys, "base_prefix", sys.prefix), os.path.dirname(sys.executable)}
    return [Path(d) for d in dirs if d]


def _programs_running_from(path: Any) -> list[str]:
    """Names of running programs whose .exe lives inside this folder."""
    try:
        import psutil
    except ImportError:
        return []
    base = _norm(path)
    found: set[str] = set()
    for proc in psutil.process_iter(["name", "exe"]):
        try:
            exe = proc.info.get("exe")
            if exe and _within(_norm(exe), base):
                found.add(proc.info.get("name") or Path(exe).name)
        except (psutil.Error, OSError):
            continue
    return sorted(found)


def _safety():
    from ..config import CONFIG
    from ..safety import SafetyEngine

    return SafetyEngine(CONFIG)


def _never_lists(engine: Any = None) -> tuple[list[str], list[str]]:
    """
    (never-touch PATHS, never-touch NAME PATTERNS) as the safety engine holds
    them. Read from the engine rather than from a copy of the YAML, so this can
    not drift from the one list; if the engine stops exposing them the answer
    is empty and make_plan refuses everything (tests/test_folder_move.py pins
    that the shipped config reads back non-empty).
    """
    engine = engine or _safety()
    paths = list(getattr(engine, "_never_paths", None) or [])
    patterns = list(getattr(engine, "_never_patterns", None) or [])
    return paths, patterns


class _Guard:
    """Asks the safety engine about names, with a prefilter built from ITS patterns for speed."""

    def __init__(self) -> None:
        self.engine = _safety()
        self.paths, self.patterns = _never_lists(self.engine)
        self.readable = bool(self.paths and self.patterns)
        self._prefilter = None
        if self.patterns:
            try:
                self._prefilter = re.compile("|".join(fnmatch.translate(p.lower()) for p in self.patterns))
            except re.error:
                self._prefilter = None

    def name_is_protected(self, name: str) -> bool:
        # protected_path is ~220 us a call (measured 2026-09-30), so only the
        # names a pattern could match are put to it. The VERDICT is always the
        # engine's; the prefilter only skips names that cannot match.
        if self._prefilter is not None and not self._prefilter.match(name.lower()):
            return False
        return self.engine.protected_path(name) is not None

    def can_be_carried(self, name: str) -> bool:
        """
        A protected FILE name that he may allow to come along: every never-touch
        pattern it matches is in CARRYABLE_PATTERNS. The engine's own verdict
        (name_is_protected) is asked first and stays the only judge of whether
        a name is protected at all; this only says which KIND of protected it
        is. A name the engine protects for any reason this cannot see (no
        pattern matches it) is not carryable.
        """
        low = name.lower()
        hits = [p.lower() for p in self.patterns if fnmatch.fnmatch(low, p.lower())]
        return bool(hits) and all(p in CARRYABLE_PATTERNS for p in hits)

    def paths_inside(self, folder: Any) -> list[str]:
        base = _norm(folder)
        return [p for p in self.paths if _within(p, base) and p != base]

    def path_is_protected(self, path: Any) -> str | None:
        return self.engine.protected_path(str(path))


# --------------------------------------------------------------------- plan
@dataclass
class Plan:
    refusal: str | None = None
    src: Path | None = None
    dst: Path | None = None
    files: int = 0
    dirs: int = 0
    bytes: int = 0
    capped: bool = False
    same_volume: bool = False
    leave_link: bool = True
    # What he asked for: None means "whatever is sensible" - a link when it
    # moves to another drive, none when it is a rename on the same one.
    link_asked: bool | None = None
    # The staging folder for this move is on disk: an earlier run had begun
    # the copy, and this one picks it up.
    resuming: bool = False
    dst_free: int = 0
    already_bytes: int = 0
    warnings: list[str] = field(default_factory=list)
    interrupted_note: str = ""
    # Where it would have gone, when that place was already taken and the move
    # was pointed at a free name beside it instead (see _free_name_beside).
    redirected_from: Path | None = None
    # Protected files (relative paths, forward slashes) the folder carries with
    # it, every one of which he is told about before he is asked.
    carried: list[str] = field(default_factory=list)
    # An earlier run already took the original aside; what is left is the end
    # of the job. `leftover` is the renamed original.
    finishing: bool = False
    leftover: Path | None = None


@dataclass
class _Scan:
    files: int = 0
    dirs: int = 0
    bytes: int = 0
    capped: bool = False
    links: int = 0
    link_example: str = ""
    placeholders: int = 0
    protected: int = 0
    protected_examples: list[str] = field(default_factory=list)
    protected_kind: str = "file"
    carried: list[str] = field(default_factory=list)
    too_many_carried: bool = False
    # The time ran out while names were still being JUDGED (not merely counted),
    # so a list of protected files found so far may be missing some.
    judging_incomplete: bool = False
    unreadable: int = 0
    venvs: int = 0


def _scan_tree(src: Path, guard: _Guard, budget_s: float | None) -> _Scan:
    """
    Count a folder and notice what makes it unsafe to move. Streams; holds no
    file list.

    Names are JUDGED (protected? a link? online-only?) in every folder except a
    dependency folder, and those are done FIRST; dependency folders (node_modules
    and the like) are only COUNTED, afterwards, so the time budget is spent on
    the part that decides the answer. His real "SAT TOP web application" is a
    .env plus a node_modules, and counting node_modules took 8.8 s.

    STOPS AT THE FIRST THING THAT WILL MAKE THE PLAN REFUSE (a protected name
    that cannot be carried, a link, an online-only file, a place that cannot be
    read): the answer is certain from then on. A protected file that CAN be
    carried is recorded and the walk goes on, because the list he is asked
    about has to be the whole list.
    """
    out = _Scan()
    deadline = None if budget_s is None else time.monotonic() + budget_s
    judged: list[tuple[str, str]] = [(str(src), "")]
    vendor: list[str] = []
    seen_entries = 0
    while judged or vendor:
        # Checked per folder as well as per 256 entries: a folder of a few
        # files never reaches the second, and a 0-second budget must still cap.
        if deadline is not None and time.monotonic() >= deadline:
            out.capped = True
            out.judging_incomplete = bool(judged)
            return out
        if judged:
            here, rel = judged.pop()
            in_deps = False
        else:
            here, rel = vendor.pop(), ""
            in_deps = True
        try:
            with os.scandir(_lp(here)) as it:
                entries = list(it)
        except OSError:
            out.unreadable += 1  # a folder that cannot be listed cannot be called safe
            return out
        out.dirs += 1
        for e in entries:
            seen_entries += 1
            if deadline is not None and seen_entries % 256 == 0 and time.monotonic() >= deadline:
                out.capped = True
                out.judging_incomplete = bool(judged) or not in_deps
                return out
            name = e.name
            try:
                st = e.stat(follow_symlinks=False)
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                out.unreadable += 1
                return out
            attrs = _attrs_of(st)
            if attrs & _REPARSE_POINT or e.is_symlink():
                out.links += 1
                out.link_example = out.link_example or name
                return out
            where = f"{rel}/{name}" if rel else name
            if not in_deps and guard.name_is_protected(name):
                if is_dir or not guard.can_be_carried(name):
                    out.protected += 1
                    out.protected_examples.append(where)
                    out.protected_kind = "folder" if is_dir else "file"
                    return out
                out.carried.append(where)
                if len(out.carried) > MAX_CARRIED:
                    out.too_many_carried = True
                    return out
            if is_dir:
                if in_deps or name.lower() in DEPENDENCY_DIRS:
                    vendor.append(os.path.join(here, name))
                else:
                    judged.append((os.path.join(here, name), where))
            else:
                out.files += 1
                out.bytes += st.st_size
                if attrs & _PLACEHOLDER_BITS:
                    out.placeholders += 1
                    return out
                if name.lower() == "pyvenv.cfg":
                    out.venvs += 1
    return out


def move_key(src: Any, dst: Any) -> str:
    """The same move is the same key however its paths are spelled, which is what lets it resume."""
    return hashlib.sha1(f"{_norm(src)}|{_norm(dst)}".encode("utf-8")).hexdigest()[:10]


def _trash_path(src: Path, key: str) -> Path:
    return src.with_name(f"{src.name}{TRASH_MARK}{key}")


def _staging_path(final: Path, key: str) -> Path:
    """Where the copy is built: beside the destination, so the last step is a rename on one volume."""
    return final.with_name(f"{final.name}{STAGING_MARK}{key}")


def _resolve_destination(text: str) -> tuple[Path | None, str | None]:
    """'D', 'd:', 'the D drive', 'drive D', a known folder ('documents') or a full path -> where it goes. Never created here."""
    raw = (text or "").strip().strip('"').strip()
    if not raw:
        return None, "Which drive or folder should it go to?"
    m = _DRIVE_WORDS.match(raw)
    if m:
        letter = (m.group(1) or m.group(2)).upper()
        root = f"{letter}:\\"
        if not os.path.isdir(root):
            return None, f"There's no {letter} drive on this machine."
        return Path(root), None
    if _LOOKS_LIKE_A_PATH.match(raw):
        expanded = os.path.expandvars(os.path.expanduser(raw))
        if os.path.isabs(expanded):
            return Path(expanded), None
    if is_known_folder_name(raw):
        name = re.sub(r"^(?:my|the)\s+", "", raw, flags=re.I)
        name = re.sub(r"\s+(?:folder|directory)$", "", name, flags=re.I).strip().lower()
        return Path.home() / _KNOWN_FOLDERS[name], None
    return None, ("I need a drive, like the D drive, a folder like Documents, or a full path, like D:\\Projects - "
                  f"\"{raw[:60]}\" isn't one I can be sure of.")


def _resolve_source(text: str) -> tuple[Path | None, str | None]:
    """A folder as he says it: a known one, a path, or a name found the way open_target finds it."""
    raw = (text or "").strip().strip('"').strip()
    if not raw:
        return None, "Which folder?"
    if _LOOKS_LIKE_A_PATH.match(raw):
        return Path(os.path.expandvars(os.path.expanduser(raw))), None
    name = re.sub(r"^(?:my|the)\s+", "", raw, flags=re.I)
    name = re.sub(r"\s+(?:folder|directory)$", "", name, flags=re.I).strip()
    if not name:
        return None, "Which folder?"
    known = _KNOWN_FOLDERS.get(name.lower())
    if known:
        return Path.home() / known, None

    from . import launcher

    hits = [h for h in launcher.find_files(name, dirs_only=True) if Path(h).is_dir()]
    hits = [h for h in hits if not any(part.lower() in DEPENDENCY_DIRS or part.lower() == ".git"
                                       for part in Path(h).parts)]
    unique: list[str] = []
    for h in hits:
        if _norm(h) not in {_norm(u) for u in unique}:
            unique.append(h)
    if not unique:
        return None, f"I couldn't find a folder called {name}."
    if len(unique) == 1:
        return Path(unique[0]), None
    depth = lambda h: len(Path(h).parts)  # noqa: E731
    shallowest = min(depth(h) for h in unique)
    top = [h for h in unique if depth(h) == shallowest]
    exact = [h for h in top if Path(h).name.lower() == name.lower()]
    if len(top) == 1:
        return Path(top[0]), None
    if len(exact) == 1:
        return Path(exact[0]), None
    listing = "; ".join(f"{Path(h).name} in {Path(h).parent.name}" for h in unique[:4])
    return None, f"I found {len(unique)} folders called {name}: {listing}. Which one?"


def _final_path(src: Path, dest: Path) -> Path:
    return dest if dest.name and dest.name.lower() == src.name.lower() else dest / src.name


def _names_the_folder_itself(src: Path, dest: Path) -> bool:
    """He gave the exact place ("D:\\Downloads"), not a place to put it in ("D")."""
    return bool(dest.name) and dest.name.lower() == src.name.lower()


# How many "<name> from C" candidates are tried before giving up and refusing.
# NOT MEASURED: a fourth move of the same folder onto one drive is a person who
# should be choosing a place, not being handed "Downloads from C 9".
FREE_NAME_TRIES = 9


def _is_occupied(path: Path) -> bool:
    """Something is there: a file, or a folder with anything in it. An empty folder is not in the way."""
    try:
        if not os.path.lexists(_lp(path)):
            return False
        if not os.path.isdir(_lp(path)):
            return True
        with os.scandir(_lp(path)) as it:
            return any(True for _ in it)
    except OSError:
        return True  # a place that cannot be listed cannot be called free


def _free_name_beside(src: Path, final: Path) -> Path | None:
    """
    "Downloads from C", then "Downloads from C 2", ... beside `final`.

    A candidate is taken only if it is free. (The first version also accepted
    one that held "the half-done copy of this move", which is how a stale
    record came to merge a move into a folder he had filled. A half-done copy
    now lives in a staging folder beside the candidate, so the candidate itself
    is free and saying the same thing again lands on the same name.) None when
    every try is taken, which is then refused like any occupied place.
    """
    letter = os.path.splitdrive(os.path.abspath(os.fspath(src)))[0][:1].upper()
    base = f"{src.name} from {letter}" if letter else f"{src.name} (moved)"
    for n in range(1, FREE_NAME_TRIES + 1):
        candidate = final.with_name(base if n == 1 else f"{base} {n}")
        if not _is_occupied(candidate):
            return candidate
    return None


def _location_problem(path: Path, role: str, guard: _Guard) -> str | None:
    """Why this path may not be moved from, or to. `role` is 'from' or 'to'."""
    norm = _norm(path)
    word = "move that" if role == "from" else "put anything there"
    if guard.path_is_protected(path):
        return f"That's on my protected list, so I won't {word}. Do it yourself in File Explorer if you really mean it."
    if len(norm) <= 3:
        return "That's a whole drive, not a folder."
    for root in _system_roots():
        if _within(norm, _norm(root)):
            # By the root's own name or its parent's - NOT anywhere in its
            # path: a root under C:\Users\x\AppData\Local\Temp is not AppData.
            if root.name.lower() == "appdata" or root.parent.name.lower() == "appdata":
                return (f"That's inside AppData, where installed apps keep their settings and sometimes the "
                        f"programs themselves, so I won't {word}.")
            return (f"{root.name or root} is part of Windows or the installed programs, and moving things in or "
                    f"out of it can stop the machine or an app from working, so I won't {word}.")
    home = _norm(Path.home())
    if norm == home or norm == _norm(Path.home().parent) or _norm(Path(norm).parent) == _norm(Path.home().parent):
        return "That's a whole user profile, not a folder inside one, so I won't move it."
    for own in [ROOT, *_own_interpreter_dirs()]:
        if _related(path, own):
            return ("That's Jalen's own folder, or the Python Jalen runs on, so I won't move it - "
                    "I'd be moving the ground I'm standing on.")
    for cloud in _cloud_sync_roots():
        if _within(norm, _norm(cloud)):
            return ("That folder is inside OneDrive. Moving it out would delete it from the cloud too, "
                    "so I won't. OneDrive's own settings can change where it lives.")
    return None


def _record_for(key: str) -> dict | None:
    return _read_state(MOVES_DIR / f"{key}.json")


def _other_move_running(src: Path, key: str) -> dict | None:
    """A DIFFERENT move of this same folder that is running now: two at once would rename the folder out from under each other."""
    try:
        records = [_read_state(p) for p in MOVES_DIR.glob("*.json")]
    except OSError:
        return None
    for rec in records:
        if (rec and rec.get("id") != key and rec.get("src")
                and _norm(rec["src"]) == _norm(src) and _effective_state(rec) == "running"):
            return rec
    return None


def _unfinished_moves(src: Path) -> list[tuple[Path, dict | None]]:
    """
    Moves of this folder that were cut short AFTER its original was renamed
    aside: (the renamed original, its record). Found by the NAME the original
    was given - <name>.jalen-moved-<key> next to where it was - so it does not
    depend on a record that may be stale, and a record that is gone still
    shows up (as None).
    """
    found: list[tuple[Path, dict | None]] = []
    prefix = f"{src.name}{TRASH_MARK}".lower()
    try:
        with os.scandir(_lp(src.parent)) as it:
            for e in it:
                if e.name.lower().startswith(prefix) and e.is_dir(follow_symlinks=False):
                    record = _record_for(e.name[len(prefix):])
                    if record is not None and not (record.get("src") and _norm(record["src"]) == _norm(src)):
                        record = None
                    found.append((Path(e.path), record))
    except OSError:
        pass
    return found


def _same_family(recorded: Path, final: Path, src: Path, exact: bool) -> bool:
    """The destination he names now leads to the place the cut-short move was going."""
    if _norm(recorded) == _norm(final):
        return True
    if exact:
        return False
    return (_norm(recorded.parent) == _norm(final.parent)
            and recorded.name.lower().startswith(f"{final.name} from ".lower()))


def make_plan(path: str, destination: str, leave_link: bool | None = None,
              budget_s: float | None = PLAN_TIME_BUDGET_S) -> Plan:
    """Everything the dry run knows. Never raises and never changes the disk."""
    try:
        return _make_plan(path, destination, leave_link, budget_s)
    except Exception as exc:  # noqa: BLE001 - errors are sentences here
        return Plan(refusal=f"I couldn't work that out ({type(exc).__name__}: {exc}), so I haven't moved anything.")


def _make_plan(path: str, destination: str, leave_link: bool | None, budget_s: float | None) -> Plan:
    plan = Plan(leave_link=True if leave_link is None else bool(leave_link), link_asked=leave_link)
    src, why = _resolve_source(path)
    if src is None:
        plan.refusal = why
        return plan
    dest, why = _resolve_destination(destination)
    if dest is None:
        plan.refusal = why
        return plan
    final = _final_path(src, dest)
    exact = _names_the_folder_itself(src, dest)

    # A move that was cut short after its original was renamed aside is
    # finished, not started again - and not refused because the old path is
    # now a link, or missing.
    left = _unfinished_moves(src)
    if left:
        done = _plan_finishing(src, final, exact, left, plan)
        if done is not None:
            return done

    # "move my Downloads to D" when D already has a Downloads (it does, on his
    # machine - a 2024 one). He named a drive, not a folder, so there is no
    # instruction to honour about the name: go beside it, and say so. If he
    # named the exact place, that is his instruction and a taken place is a
    # refusal. Merging is the other way out and is not taken - two copies of
    # "report.pdf" with different contents have no right answer, and
    # verification could not tell his files from mine.
    return _plan_core(src, final, plan, budget_s, beside_if_taken=not exact)


def _plan_finishing(src: Path, final: Path, exact: bool, left: list[tuple[Path, dict | None]],
                    plan: Plan) -> Plan | None:
    """The plan for finishing a cut-short move, a refusal if one blocks this one, or None if none is his."""
    guard = _Guard()
    if not guard.readable:
        return None  # _plan_core says so, in its own words
    for trash, record in left:
        if record is None:
            continue
        recorded = Path(record["dst"])
        if not _same_family(recorded, final, src, exact):
            continue
        if _effective_state(record) == "running":
            plan.refusal = (f"That move is already running - {_progress_words(record)}. "
                            "Say move status to check on it.")
            return plan
        for place, role in ((src, "from"), (recorded, "to")):
            if problem := _location_problem(place, role, guard):
                plan.refusal = problem
                return plan
        plan.src, plan.dst = src, recorded
        plan.finishing, plan.leftover = True, trash
        plan.leave_link = bool(record.get("leave_link", True))
        plan.files, plan.bytes = int(record.get("files_total") or 0), int(record.get("bytes_total") or 0)
        plan.carried = [str(c) for c in record.get("carry") or []]
        plan.same_volume = False
        plan.dst_free = _free_bytes(recorded) if os.path.exists(_nearest_existing(recorded)) else 0
        return plan
    # A cut-short move of this folder, but not to where he is saying now - and
    # the old path no longer leads to a real folder of its own, so there is
    # nothing else this could mean.
    known = [r for _, r in left if r is not None]
    gone_or_link = (not src.exists()) or _is_reparse(src)
    if known and gone_or_link:
        recorded = Path(known[0]["dst"])
        plan.refusal = (f"An earlier move of {src.name} to {recorded} was cut short and the original is still "
                        f"waiting to be removed, so that one has to be finished first. Say move my {src.name} "
                        f"to {_drive_word(recorded).replace('the ', '').replace(' drive', '')} to finish it.")
        return plan
    if not known and gone_or_link:
        plan.refusal = (f"There's a folder called {left[0][0].name} left from a move that was cut short, and I no "
                        "longer have the record of where it was going, so I can't finish it. Nothing was lost: "
                        f"it is the original {src.name}.")
        return plan
    return None


def _plan_core(src: Path, final: Path, plan: Plan, budget_s: float | None,
               beside_if_taken: bool = False, agreed: set[str] | None = None,
               as_job: bool = False) -> Plan:
    """
    Every check, in the order that matters. `beside_if_taken` is set only by the
    dry run and by move_folder: the job (make_plan_for) is handed the exact
    place its record names and must never pick a different one. `agreed` is the
    set of protected files he was told about; the dry run leaves it None and
    lists whatever it finds, the job passes it and refuses anything else.
    """
    guard = _Guard()
    plan.src, plan.dst = src, final

    # Protection BEFORE existence: a guarded path must not be confirmed to exist.
    if not guard.readable:
        plan.refusal = ("I can't read my own protected list right now, so I won't move anything. "
                        "That's a fault in me, not in your folder.")
        return plan
    if problem := _location_problem(src, "from", guard):
        plan.refusal = problem
        return plan
    if not src.exists():
        plan.refusal = f"There's no folder at {src}."
        return plan
    if _is_reparse(src):
        target = os.path.realpath(src) if _is_junction(src) else "somewhere else"
        plan.refusal = (f"{src.name} is already a link to {target}, so it looks as if it has been moved already.")
        return plan
    if not src.is_dir():
        plan.refusal = f"{src.name} is a file, not a folder - use move_file for a single file."
        return plan
    if _norm(src) == _norm(final):
        plan.refusal = f"{src.name} is already there."
        return plan
    if _within(_norm(final), _norm(src)):
        plan.refusal = "The destination is inside the folder being moved, which can't work."
        return plan
    if _within(_norm(src), _norm(final)):
        plan.refusal = "The folder being moved is inside the destination already."
        return plan
    if str(final).startswith("\\\\") or not _is_fixed_drive(final):
        plan.refusal = ("That destination isn't a fixed drive - it looks like a USB stick or a network share. "
                        "A link to it would break the moment it was unplugged, so I won't move a folder there.")
        return plan
    if problem := _location_problem(final, "to", guard):
        plan.refusal = problem
        return plan

    # Only now - after the place has been judged by its own name and found
    # allowed - is it looked at, so a protected folder is never listed to find
    # out whether it is "occupied". Then the new name is judged the same way.
    if beside_if_taken and _is_occupied(final):
        beside = _free_name_beside(src, final)
        if beside is not None:
            plan.redirected_from, final = final, beside
            plan.dst = final
            if problem := _location_problem(final, "to", guard):
                plan.refusal = problem
                return plan

    # The same questions about where each path REALLY leads. Everything above
    # judged the spelling: a destination that is a junction into C:\Windows, or
    # into the folder being moved, reads as innocent, and "C:\PROGRA~1" is not
    # the string "C:\Program Files" (realpath expands 8.3 names, measured).
    real_src, real_final = Path(os.path.realpath(src)), Path(os.path.realpath(final))
    for spelled, real, role in ((src, real_src, "from"), (final, real_final, "to")):
        if _norm(real) != _norm(spelled):
            if problem := _location_problem(real, role, guard):
                plan.refusal = problem
                return plan
    if _within(_norm(real_final), _norm(real_src)):
        plan.refusal = "The destination is inside the folder being moved, which can't work."
        return plan
    if _within(_norm(real_src), _norm(real_final)):
        plan.refusal = "The folder being moved is inside the destination already."
        return plan
    if busy := _other_move_running(src, move_key(src, final)):
        plan.refusal = (f"{src.name} is already being moved to {busy.get('dst')}. Wait for that to finish - say "
                        "move status to check - or stop it first.")
        return plan
    if guard.paths_inside(src):
        plan.refusal = ("Something inside that folder is on my protected list, and the list is by location, "
                        "so moving the folder would quietly stop protecting it. I won't.")
        return plan
    for system_root in _system_roots():
        if _within(_norm(system_root), _norm(src)):
            plan.refusal = (f"That folder contains {system_root.name or system_root}, part of Windows or the "
                            "installed programs, so I won't move it.")
            return plan
    running = _programs_running_from(src)
    if running:
        plan.refusal = (f"{', '.join(running[:SPOKEN_EXAMPLES])} is running from inside that folder. "
                        "Close it first and ask me again.")
        return plan

    scan = _scan_tree(src, guard, budget_s)
    plan.files, plan.dirs, plan.bytes, plan.capped = scan.files, scan.dirs, scan.bytes, scan.capped
    # The scan stops at the first of these, so each is "at least one".
    if scan.unreadable:
        plan.refusal = ("I couldn't read a place inside that folder (permissions, probably), so I can't be sure "
                        "it's safe to move. I won't.")
        return plan
    if scan.protected:
        plan.refusal = (f"It holds a protected {scan.protected_kind}, {scan.protected_examples[0]}, and I never "
                        "move those. Move this folder yourself in File Explorer, or take that "
                        f"{scan.protected_kind} out of it first.")
        return plan
    if scan.too_many_carried:
        plan.refusal = (f"It holds more than {MAX_CARRIED} protected files (environment and token files). I only "
                        "carry those after naming each one for a yes, and that's too many to name, so I won't "
                        "move it from here.")
        return plan
    if scan.carried and scan.judging_incomplete:
        plan.refusal = (f"It holds protected files ({_names(scan.carried)}) and it's too big for me to look through "
                        f"every folder of it in the {PLAN_TIME_BUDGET_S:.0f} seconds I give a dry run, and I only "
                        "carry them along after naming every one. I won't move it from here.")
        return plan
    if scan.links:
        plan.refusal = (f"It contains a link ({scan.link_example}) pointing elsewhere - a junction or a "
                        "symbolic link - and I can't copy those faithfully. I won't move it.")
        return plan
    if scan.placeholders:
        plan.refusal = ("It has online-only cloud files in it. Copying them would download every one first, "
                        "so I won't move it from here.")
        return plan
    plan.carried = list(scan.carried)
    if agreed is not None:
        unheard = [c for c in plan.carried if c.lower() not in agreed]
        if unheard:
            plan.refusal = (f"It holds {'a protected file' if len(unheard) == 1 else 'protected files'} I didn't "
                            f"tell you about, {_names(unheard)}, and I only carry the ones you've heard named, so I "
                            "won't. Ask me again and I'll list what's in there.")
            return plan
    if scan.venvs:
        plan.warnings.append(
            "It has a Python virtual environment inside, and those stop working when they move - "
            "it will need recreating afterwards.")

    key = move_key(src, final)
    record = _record_for(key)
    if record is not None:
        status = _effective_state(record)
        # `as_job` is the job's own pre-flight (run_move): the record it
        # finds is ITS record, alive because it is this very process, and
        # refusing it as "already running" refused every move it started.
        if status == "running" and not as_job:
            plan.refusal = (f"That move is already running - {_progress_words(record)}. "
                            "Say move status to check on it.")
            return plan
        if status in ("interrupted", "failed", "blocked"):
            plan.interrupted_note = (
                f"An earlier attempt at this move was interrupted"
                f"{' at ' + _progress_words(record) if record.get('bytes_total') else ''}; "
                "I'll pick up where it stopped.")

    # Whatever is at the destination is his, never ours: the copy is built in a
    # staging folder beside it and renamed onto it at the end, so there is no
    # "resume into what is there" - nothing there is ever ours. A record, however
    # recent or stale, changes none of this.
    staging = _staging_path(final, key)
    plan.resuming = os.path.isdir(_lp(staging))
    plan.same_volume = _same_volume(src, final)
    if plan.link_asked is None:
        plan.leave_link = not plan.same_volume
    if _is_occupied(final):
        plan.refusal = (f"There's already something at {final}. I won't merge a move into it - "
                        "pick another place or clear that one first.")
        return plan
    if not plan.same_volume:
        plan.already_bytes = _tree_bytes(staging) if plan.resuming else 0
        needed = max(0, plan.bytes - plan.already_bytes) + HEADROOM_BYTES
        plan.dst_free = _free_bytes(final)
        if plan.dst_free < needed:
            plan.refusal = (f"There isn't room: it needs {_say_bytes(max(0, plan.bytes - plan.already_bytes))} "
                            f"and {_drive_word(final)} has {_say_bytes(plan.dst_free)} free, and I keep "
                            f"{_say_bytes(HEADROOM_BYTES)} free there.")
            return plan
    return plan


def _tree_bytes(folder: Path) -> int:
    total = 0
    stack = [str(folder)]
    while stack:
        here = stack.pop()
        try:
            with os.scandir(_lp(here)) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(os.path.join(here, e.name))
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _carried_sentence(carried: list[str]) -> str:
    return (f"It has {_count(len(carried), 'protected file')} that will be carried over unread: {_names(carried)}.")


def describe_plan(plan: Plan) -> str:
    """The sentence he hears before he is asked to confirm anything."""
    if plan.refusal:
        return plan.refusal
    assert plan.src is not None and plan.dst is not None
    if plan.finishing:
        parts = [f"An earlier move of {plan.src.name} to {plan.dst} was cut short after it had been copied and "
                 f"checked. What's left is taking the old copy off {_drive_word(plan.src)} "
                 f"(it's renamed {plan.leftover.name if plan.leftover else 'something with jalen-moved in it'}).",
                 "Before it removes anything it checks that everything still in the old copy is on the new drive, "
                 "and it removes nothing that isn't."]
        if plan.leave_link:
            parts.append(f"A link will stay at {plan.src}, so programs that look there still find it.")
        if plan.carried:
            parts.append(_carried_sentence(plan.carried))
        parts.append("I haven't changed anything yet.")
        return " ".join(parts)
    size = f"{'at least ' if plan.capped else ''}{_count(plan.files, 'file')}, {_say_bytes(plan.bytes)}"
    parts = [f"Moving {plan.src.name} to {plan.dst}: {size}."]
    if plan.redirected_from is not None:
        parts.append(f"{plan.redirected_from} is already there with other things in it, and I never mix a move "
                     "into a folder that exists, so it goes in a new one beside it. Yours is untouched.")
    if plan.same_volume:
        parts.append("That's the same drive, so it's an instant rename and frees nothing.")
    else:
        parts.append(f"That frees {'at least ' if plan.capped else ''}{_say_bytes(plan.bytes)} on "
                     f"{_drive_word(plan.src)}, and {_drive_word(plan.dst)} has {_say_bytes(plan.dst_free)} free.")
        parts.append("I copy everything first, check it against the original, and only then remove the old "
                     "folder, so nothing is lost if it stops halfway.")
    if plan.leave_link:
        parts.append(f"A link stays at {plan.src}, so programs that look there still find it.")
    else:
        parts.append(f"I won't leave a link, so {plan.src} will simply be gone.")
    if plan.carried:
        parts.append(_carried_sentence(plan.carried) +
                     " I copy them byte for byte and check them the same way, but I never read them out.")
    if plan.capped:
        parts.append(f"I stopped counting after {PLAN_TIME_BUDGET_S:.0f} seconds, so those are at least figures.")
    if plan.interrupted_note:
        parts.append(plan.interrupted_note)
    parts.extend(plan.warnings)
    parts.append("I haven't moved anything.")
    return " ".join(parts)


# ----------------------------------------------------------------- the copy
@dataclass
class CopyResult:
    copied: int = 0
    skipped: int = 0
    bytes_copied: int = 0
    errors: list[str] = field(default_factory=list)


def _already_there(dst: str, st: os.stat_result) -> bool:
    try:
        d = os.stat(_lp(dst))
    except OSError:
        return False
    return (stat.S_ISREG(d.st_mode) and d.st_size == st.st_size
            and abs(d.st_mtime_ns - st.st_mtime_ns) <= MTIME_TOLERANCE_NS)


def _copy_one(src: str, dst: str) -> None:
    """One file, via a part file and a rename, so a half-written file is never taken for a whole one."""
    part = dst + PART_SUFFIX
    shutil.copyfile(_lp(src), _lp(part))
    shutil.copystat(_lp(src), _lp(part))
    try:
        os.replace(_lp(part), _lp(dst))
    except PermissionError:
        # An earlier, different copy of a read-only file is in the way.
        os.chmod(_lp(dst), stat.S_IWRITE)
        os.replace(_lp(part), _lp(dst))


def copy_tree(src: Any, dst: Any, progress: Callable[[int, int], None] | None = None) -> CopyResult:
    """
    Copy src into dst, skipping what is already there. Resumable by construction.
    The job only ever calls this with a staging folder as `dst`: a folder this
    module made, so nothing it replaces was ever anyone else's.
    """
    res = CopyResult()
    done_files = done_bytes = 0
    # In a folder this module made, a file where a folder belongs (or the other
    # way round) is a leftover of an earlier try, and goes. Anywhere else it is
    # reported, never removed.
    ours = STAGING_MARK in os.path.basename(os.fspath(dst))
    stack: list[tuple[str, str, bool]] = [(os.fspath(src), os.fspath(dst), False)]
    while stack:
        s, d, post = stack.pop()
        if post:
            try:
                shutil.copystat(_lp(s), _lp(d))
            except OSError:
                pass
            continue
        try:
            if ours and os.path.lexists(_lp(d)) and not os.path.isdir(_lp(d)):
                _remove_any(d)
            os.makedirs(_lp(d), exist_ok=True)
        except OSError as exc:
            res.errors.append(f"{os.path.basename(d)}: {exc}")
            continue
        stack.append((s, d, True))
        try:
            with os.scandir(_lp(s)) as it:
                entries = list(it)
        except OSError as exc:
            res.errors.append(f"{os.path.basename(s)}: {exc}")
            continue
        for e in entries:
            name = e.name
            sp, dp = os.path.join(s, name), os.path.join(d, name)
            try:
                st = e.stat(follow_symlinks=False)
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError as exc:
                res.errors.append(f"{name}: {exc}")
                continue
            if _attrs_of(st) & _REPARSE_POINT or e.is_symlink():
                res.errors.append(f"{name}: is a link, which I don't copy")
                continue
            if is_dir:
                stack.append((sp, dp, False))
                continue
            if ours and os.path.isdir(_lp(dp)) and not os.path.islink(_lp(dp)):
                _remove_any(dp)
            if _already_there(dp, st):
                res.skipped += 1
            else:
                err = _copy_file(sp, dp)
                if err:
                    res.errors.append(f"{name}: {err}")
                    continue
                res.copied += 1
                res.bytes_copied += st.st_size
            done_files += 1
            done_bytes += st.st_size
            if progress:
                progress(done_files, done_bytes)
    return res


def _copy_file(sp: str, dp: str) -> str | None:
    """Copy one file, trying again if it changed under us. Returns why it failed, or None."""
    last = "it kept changing while I copied it"
    for attempt in range(COPY_RETRIES + 1):
        if attempt:
            time.sleep(RETRY_PAUSE_S)
        try:
            before = os.stat(_lp(sp))
            _copy_one(sp, dp)
            after = os.stat(_lp(sp))
        except OSError as exc:
            last = str(exc).strip() or type(exc).__name__
            continue
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            last = "it kept changing while I copied it"
            continue
        return None
    return last


# --------------------------------------------------------------- the verify
@dataclass
class VerifyResult:
    files: int = 0
    dirs: int = 0
    problems: list[tuple[str, str]] = field(default_factory=list)
    problem_count: int = 0
    hashed_files: int = 0
    hashed_bytes: int = 0
    # Protected files compared byte for byte (never hashed, never read out).
    compared_files: int = 0


_MAX_PROBLEMS_KEPT = 200


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(_lp(path), "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _same_bytes(a: str, b: str) -> bool:
    """
    Do these two files hold the same bytes? Compared a chunk at a time and
    answered with a bool: what is in them is not kept, hashed, logged or said.
    This is how a protected file that was carried along is checked.
    """
    try:
        with open(_lp(a), "rb") as fa, open(_lp(b), "rb") as fb:
            while True:
                ca, cb = fa.read(1 << 20), fb.read(1 << 20)
                if ca != cb:
                    return False
                if not ca:
                    return True
    except OSError:
        return False


def _listing(folder: str) -> dict[str, os.stat_result | None]:
    out: dict[str, tuple[bool, os.stat_result]] = {}
    with os.scandir(_lp(folder)) as it:
        for e in it:
            try:
                out[e.name] = (e.is_dir(follow_symlinks=False), e.stat(follow_symlinks=False))
            except OSError:
                continue
    return out  # type: ignore[return-value]


def verify_trees(src: Any, dst: Any, seed: str = "", sample: bool = True, *, full: bool = False,
                 subset: bool = False, agreed: set[str] | None = None) -> VerifyResult:
    """
    Is dst a faithful copy of src? Size and modified time of every file, every
    folder, nothing extra - and then the content:

      full     every file, by SHA-256 on both sides (protected ones are compared
               byte for byte instead - see _same_bytes)
      sample   (when not full) a SHA-256 of a sample, within SAMPLE_BYTE_BUDGET
      subset   dst may hold MORE than src. This is the check before the old
               copy is deleted, and a half-deleted old copy is still a subset
               of the new one.
      agreed   the protected files he was told about (lower-case relative
               paths). Any other protected name outside a dependency folder is
               reported as "unnamed" and never repaired.

    Returns problems; never decides what to do about them.
    """
    res = VerifyResult()
    guard = _Guard() if (sample or full or agreed is not None) else None
    rng = random.Random(seed)
    reservoir: list[tuple[int, str, str]] = []
    largest: list[tuple[int, str, str]] = []
    seen_candidates = 0

    def problem(kind: str, rel: str) -> None:
        res.problem_count += 1
        if len(res.problems) < _MAX_PROBLEMS_KEPT:
            res.problems.append((kind, rel))

    stack: list[tuple[str, str, str, bool]] = [("", os.fspath(src), os.fspath(dst), False)]
    while stack:
        rel, s, d, in_deps = stack.pop()
        res.dirs += 1
        try:
            left, right = _listing(s), _listing(d)
        except OSError:
            problem("missing", rel or ".")
            continue
        if not subset:
            for name in right.keys() - left.keys():
                problem("extra", f"{rel}/{name}".lstrip("/"))
        for name, (s_dir, s_st) in left.items():
            where = f"{rel}/{name}".lstrip("/")
            protected = (guard is not None and not in_deps and guard.name_is_protected(name))
            if protected and agreed is not None and (s_dir or where.lower() not in agreed):
                problem("unnamed", where)
                continue
            if name not in right:
                problem("missing", where)
                continue
            d_dir, d_st = right[name]
            if s_dir != d_dir:
                problem("kind", where)
            elif s_dir:
                stack.append((where, os.path.join(s, name), os.path.join(d, name),
                              in_deps or name.lower() in DEPENDENCY_DIRS))
            else:
                res.files += 1
                if (s_st.st_size != d_st.st_size
                        or abs(s_st.st_mtime_ns - d_st.st_mtime_ns) > MTIME_TOLERANCE_NS):
                    problem("differs", where)
                elif guard is None:
                    continue
                elif protected:
                    res.compared_files += 1
                    if not _same_bytes(os.path.join(s, name), os.path.join(d, name)):
                        problem("hash", where)
                elif guard.name_is_protected(name):
                    continue  # looks protected inside a dependency folder: size and date only, never read
                elif full:
                    try:
                        same = _hash_file(os.path.join(s, name)) == _hash_file(os.path.join(d, name))
                    except OSError:
                        same = False
                    res.hashed_files += 1
                    res.hashed_bytes += s_st.st_size
                    if not same:
                        problem("hash", where)
                elif sample:
                    cand = (s_st.st_size, os.path.join(s, name), os.path.join(d, name))
                    seen_candidates += 1
                    if len(reservoir) < SAMPLE_FILES:
                        reservoir.append(cand)
                    else:
                        slot = rng.randrange(seen_candidates)
                        if slot < SAMPLE_FILES:
                            reservoir[slot] = cand
                    if len(largest) < SAMPLE_LARGEST:
                        heapq.heappush(largest, cand)
                    else:
                        heapq.heappushpop(largest, cand)

    if guard is not None and sample and not full:
        chosen = {c[1]: c for c in reservoir + largest}
        budget = SAMPLE_BYTE_BUDGET
        for size, sp, dp in sorted(chosen.values()):
            if size > budget:
                continue
            budget -= size
            try:
                same = _hash_file(sp) == _hash_file(dp)
            except OSError:
                same = False
            res.hashed_files += 1
            res.hashed_bytes += size
            if not same:
                problem("hash", os.path.relpath(dp, os.fspath(dst)).replace("\\", "/"))
    return res


# ----------------------------------------------------------------- records
def _write_atomic(path: Path, record: dict) -> bool:
    """Write a record. True if it is on disk. A full C: (his normal state) is False, not an exception."""
    tmp = path.with_name(f"{path.stem}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            # A reader (the status tool) has it open for a moment; Windows will not replace under it.
            time.sleep(0.05)
        except OSError:
            break
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    return False


def _read_state(path: Any) -> dict | None:
    try:
        blob = json.loads(Path(path).read_text(encoding="utf-8"))
        return blob if isinstance(blob, dict) else None
    except (OSError, ValueError):
        return None


def _update_state(path: Any, **fields: Any) -> None:
    """
    Progress, not the move: what a resumed run does is read from the disk, so a
    record that could not be written costs a status line and nothing else.
    Raising here once stranded a move between the link and the delete, on a
    drive that was full.
    """
    try:
        record = _read_state(path) or {}
        record.update(fields)
        _write_atomic(Path(path), record)
    except OSError:
        pass


def _self_identity() -> tuple[int, float]:
    try:
        import psutil

        return os.getpid(), psutil.Process().create_time()
    except Exception:  # noqa: BLE001
        return os.getpid(), 0.0


def _alive(record: dict) -> bool:
    pid, started = record.get("pid"), record.get("pid_started")
    if not pid:
        return False
    try:
        import psutil

        if not psutil.pid_exists(int(pid)):
            return False
        return abs(psutil.Process(int(pid)).create_time() - float(started or 0)) < 2.0
    except Exception:  # noqa: BLE001
        return False


_LIVE_STATES = ("queued", "starting", "copying", "verifying", "switching", "cleaning")


def _effective_state(record: dict) -> str:
    """The recorded state, corrected for a process that is no longer there to be in it."""
    state = record.get("state", "")
    if state not in _LIVE_STATES:
        return state
    if _alive(record):
        return "running"
    if state == "queued" and not record.get("pid") and time.time() - float(record.get("updated_at", 0)) < STALE_QUEUE_S:
        return "running"
    return "interrupted"


def _new_state(plan: Plan) -> Path | None:
    """Write the record a job will find, and return its path (None if it could not be written). Same move, same file."""
    assert plan.src is not None and plan.dst is not None
    key = move_key(plan.src, plan.dst)
    path = MOVES_DIR / f"{key}.json"
    try:
        wrote = _write_atomic(path, {
            "id": key, "src": str(plan.src), "dst": str(plan.dst), "leave_link": plan.leave_link,
            "carry": list(plan.carried),
            "state": "queued", "files_total": plan.files, "bytes_total": plan.bytes,
            "files_done": 0, "bytes_done": 0, "pid": None, "pid_started": None,
            "queued_at": time.time(), "updated_at": time.time(), "result": "",
        })
    except OSError:
        wrote = False
    return path if wrote else None


def _progress_words(record: dict) -> str:
    total = record.get("bytes_total") or 0
    if not total:
        return "just started"
    pct = round(100 * (record.get("bytes_done") or 0) / total)
    return (f"{pct} percent, {_say_bytes(record.get('bytes_done') or 0)} of {_say_bytes(total)}")


# ------------------------------------------------------------------ the job
def _make_junction(link: Path, target: Path) -> str | None:
    """A junction at `link` leading to `target`. Returns why not, or None. No shell: paths are never parsed by cmd."""
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def _delete_tree(path: Any) -> list[str]:
    """Remove a folder, clearing read-only flags on the way (.git objects are read-only). Returns what stayed."""
    stayed: list[str] = []

    def onexc(func: Any, p: str, exc: BaseException) -> None:
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            stayed.append(p)

    shutil.rmtree(_lp(path), onexc=onexc)
    if os.path.exists(_lp(path)) and not stayed:
        stayed.append(os.fspath(path))
    return stayed


def _remove_any(path: str) -> None:
    """Remove a file or a folder, read-only or not. Only ever called inside a staging folder."""
    target = _lp(path)
    try:
        if os.path.isdir(target) and not os.path.islink(target):
            def onexc(func: Any, p: str, exc: BaseException) -> None:
                try:
                    os.chmod(p, stat.S_IWRITE)
                    func(p)
                except OSError:
                    pass

            shutil.rmtree(target, onexc=onexc)
        else:
            os.chmod(target, stat.S_IWRITE)
            os.unlink(target)
    except OSError:
        pass


def _repair(src: Path, dst: Path, problems: list[tuple[str, str]]) -> None:
    """
    Make what verification found wrong into something copy_tree will fix.

    ONLY EVER TOUCHES A STAGING FOLDER, and refuses anything else: everything in
    a folder named <final>.jalen-incoming-<key> was put there by this module, so
    it may be deleted - the copy of a file that does not match (it is about to be
    copied again), a half-written part file, and a file the source no longer has
    (a download renamed after it was copied). A folder that is not a staging
    folder may hold his own files, and none of them is ever removed here.
    """
    if STAGING_MARK not in os.path.basename(os.fspath(dst)):
        return
    for kind, rel in problems:
        if kind in ("hash", "differs", "kind", "extra"):
            _remove_any(os.path.join(os.fspath(dst), *rel.split("/")))


class _Throttle:
    def __init__(self, record_path: Path) -> None:
        self.path = record_path
        self.last = 0.0

    def __call__(self, files_done: int, bytes_done: int) -> None:
        now = time.monotonic()
        if now - self.last >= PROGRESS_EVERY_S:
            self.last = now
            _update_state(self.path, files_done=files_done, bytes_done=bytes_done, updated_at=time.time())


def _finish(path: Path, state: str, code: int, sentence: str) -> int:
    _update_state(path, state=state, result=sentence, returncode=code, updated_at=time.time())
    _say("RESULT: " + sentence)
    return code


def _problem_sentence(problems: list[tuple[str, str]], count: int) -> str:
    """What was wrong, as a clause with no full stop, by path only - never by what is in a file."""
    unnamed = [rel for kind, rel in problems if kind == "unnamed"]
    if unnamed:
        return (f"a protected file turned up in that folder that I hadn't told you about ({_names(unnamed)}); "
                "say the move again and I'll list what's in there")
    names = ", ".join(rel for _, rel in problems[:SPOKEN_EXAMPLES])
    return f"{_count(count, 'file')} on the new drive didn't match the originals (for example {names})"


def run_move(state_path: Any) -> int:
    """
    The background job: carry out the move a record describes. Returns 0 only
    when the move is complete and the old folder is gone; anything else leaves
    the original exactly as it was, or says precisely what is left.

    An error nobody planned for is a sentence, not a traceback: the job's own
    notes are what he hears, and "it didn't say how it went" is not an answer.
    Nothing here removes the old copy except after the check in
    _finish_after_aside, so an exception anywhere earlier removed nothing.
    """
    try:
        return _run_move(state_path)
    except Exception as exc:  # noqa: BLE001 - BaseException (a kill) is deliberately not caught
        return _finish(Path(state_path), "failed", 1, (
            f"It stopped unexpectedly ({type(exc).__name__}: {str(exc)[:160]}). Nothing in the old place was "
            "removed unless it was already safe on the new drive. Say move status to see where things are, and "
            "say the move again to pick it up."))


def _run_move(state_path: Any) -> int:
    path = Path(state_path)
    record = _read_state(path)
    if not record or not record.get("src") or not record.get("dst"):
        _say("RESULT: I couldn't read the record of that move, so I haven't touched anything.")
        return 2
    src, dst = Path(record["src"]), Path(record["dst"])
    leave_link = bool(record.get("leave_link", True))
    key = record.get("id") or move_key(src, dst)
    trash, staging = _trash_path(src, key), _staging_path(dst, key)
    agreed = {str(c).lower() for c in record.get("carry") or []}
    pid, started = _self_identity()
    _update_state(path, state="starting", pid=pid, pid_started=started, updated_at=time.time())

    # The source has already been renamed aside by an earlier run: its copy was
    # verified then, so what is left is the end of the job.
    if os.path.isdir(_lp(trash)):
        return _finish_after_aside(path, src, dst, trash, staging, key, leave_link, record, agreed)

    plan = make_plan_for(src, dst, leave_link, agreed)
    if plan.refusal:
        return _finish(path, "failed", 2, f"I stopped before copying anything: {plan.refusal}")
    _update_state(path, state="copying", files_total=plan.files, bytes_total=plan.bytes,
                  files_done=0, bytes_done=0)
    _say(f"copying {plan.files} files, {plan.bytes} bytes")

    throttle = _Throttle(path)
    result = copy_tree(src, staging, throttle)
    if result.errors:
        shown = "; ".join(result.errors[:SPOKEN_EXAMPLES])
        return _finish(path, "failed", 5, (
            f"{_count(len(result.errors), 'file')} couldn't be copied (for example {shown}), so nothing was "
            "removed from the old place. If a program has them open, close it and say the move again - "
            "it will pick up where it stopped."))
    _update_state(path, files_done=plan.files, bytes_done=plan.bytes)

    full = plan.bytes <= FULL_HASH_UP_TO_BYTES
    _update_state(path, state="verifying")
    verdict = verify_trees(src, staging, seed=key, full=full, agreed=agreed)
    for _ in range(MAX_VERIFY_PASSES - 1):
        if not verdict.problems or any(kind == "unnamed" for kind, _ in verdict.problems):
            break
        _say(f"repairing {verdict.problem_count} problems")
        _repair(src, staging, verdict.problems)
        again = copy_tree(src, staging)
        if again.errors:
            break
        verdict = verify_trees(src, staging, seed=key, full=full, agreed=agreed)
    if verdict.problems:
        return _finish(path, "failed", 4, (
            "I stopped before removing anything: " + _problem_sentence(verdict.problems, verdict.problem_count)
            + ". The original is untouched, and nothing was removed. Say the move again to retry."))
    _update_state(path, hashed_files=verdict.hashed_files, files_verified=verdict.files,
                  protected_compared=verdict.compared_files)
    return _switch_over(path, src, dst, trash, staging, key, leave_link, _read_state(path) or record, agreed)


def make_plan_for(src: Path, final: Path, leave_link: bool, agreed: set[str] | None = None) -> Plan:
    """The job's own pre-flight: the same checks as the dry run, on the final paths, with no time budget."""
    try:
        return _plan_core(src, final, Plan(leave_link=leave_link, link_asked=leave_link), None,
                          agreed=agreed if agreed is not None else set(), as_job=True)
    except Exception as exc:  # noqa: BLE001
        return Plan(refusal=f"{type(exc).__name__}: {exc}")


def _switch_over(path: Path, src: Path, dst: Path, trash: Path, staging: Path, key: str,
                 leave_link: bool, record: dict, agreed: set[str]) -> int:
    _update_state(path, state="switching")
    if not os.path.isdir(_lp(trash)):
        try:
            os.rename(_lp(src), _lp(trash))
        except OSError as exc:
            return _finish(path, "blocked", 3, (
                "Everything is copied and checked on the new drive, but I couldn't take the old folder away "
                f"because something is using it ({str(exc).strip() or type(exc).__name__}). Nothing was lost. "
                "Close whatever has it open and say the move again to finish."))
    return _finish_after_aside(path, src, dst, trash, staging, key, leave_link, record, agreed)


def _place(staging: Path, final: Path) -> str | None:
    """Rename the checked copy onto its place. Returns why not, or None. Never replaces anything that is there."""
    try:
        if os.path.lexists(_lp(final)):
            if os.path.isdir(_lp(final)) and not os.path.islink(_lp(final)) and not _is_occupied(final):
                os.rmdir(_lp(final))  # empty: not in the way
            else:
                return f"Something is at {final} now, and I don't put a move on top of anything."
        os.rename(_lp(staging), _lp(final))
    except OSError as exc:
        return f"I couldn't put the checked copy in place ({str(exc).strip() or type(exc).__name__})."
    return None


def _back_out(path: Path, src: Path, trash: Path, dst: Path, staging: Path, placed_now: bool, what: str) -> int:
    """
    Something went wrong after the original was renamed aside: put it back,
    and if the copy had just been placed, put that back in its holding folder
    too, so the move is exactly where it was before this step and can be said
    again. Says what actually ended up where.
    """
    said = _where_it_is(trash, src)
    if placed_now:
        if said.startswith("I put the original back"):
            try:
                os.rename(_lp(dst), _lp(staging))
                said += " The checked copy went back to its holding folder beside the destination."
            except OSError:
                said += f" The checked copy is at {dst}."
        else:
            said += f" The checked copy is at {dst}."
    return _finish(path, "failed", 4, f"{what} {said} Nothing was removed. Say the move again to retry.")


def _finish_after_aside(path: Path, src: Path, dst: Path, trash: Path, staging: Path, key: str,
                        leave_link: bool, record: dict, agreed: set[str]) -> int:
    """
    The original is renamed aside (by this run or an earlier one). Check, place,
    link, delete - and every step reads the disk rather than trusting that the
    last run got as far as it thought.
    """
    _update_state(path, state="switching")
    placed_now = False
    if os.path.isdir(_lp(staging)):
        # The renamed original cannot change any more, except through a program
        # that holds a file in it open and is allowed to keep writing (Windows
        # lets a file opened with delete-sharing follow its folder's rename;
        # NOT MEASURED here). So this is not "the check with no race in it" -
        # it is the last full look, and the one before the delete is the last
        # look of all.
        frozen = verify_trees(trash, staging, sample=False, agreed=agreed)
        if frozen.problems and not any(kind == "unnamed" for kind, _ in frozen.problems):
            _repair(trash, staging, frozen.problems)
            copy_tree(trash, staging)
            # Something changed, so this one reads content again: everything
            # when the folder is small enough, the sample otherwise.
            frozen = verify_trees(trash, staging, seed=key,
                                  full=int(record.get("bytes_total") or 0) <= FULL_HASH_UP_TO_BYTES,
                                  agreed=agreed)
        if frozen.problems:
            return _back_out(path, src, trash, dst, staging, False, (
                "The last check, after taking the old folder aside, stopped it: "
                + _problem_sentence(frozen.problems, frozen.problem_count) + "."))
        why = _place(staging, dst)
        if why:
            return _back_out(path, src, trash, dst, staging, False,
                             f"{why} The checked copy is waiting in {staging.name}, beside it.")
        placed_now = True
        _update_state(path, placed=True)
    elif not os.path.isdir(_lp(dst)):
        return _back_out(path, src, trash, dst, staging, False,
                         f"The checked copy isn't at {dst} or beside it any more.")

    # Whatever is left of the old copy must be on the new drive. A first run has
    # all of it; a run that was cut during the delete has less; either way the
    # delete below can only remove what is safely somewhere else.
    left = verify_trees(trash, dst, sample=False, subset=True)
    if left.problems:
        what = ("Part of what is left of the old copy isn't on the new drive "
                f"(for example {', '.join(rel for _, rel in left.problems[:SPOKEN_EXAMPLES])}), so I'm not removing any more of it.")
        if placed_now:
            return _back_out(path, src, trash, dst, staging, True, what)
        return _finish(path, "failed", 4, (
            f"{what} The old copy is still at {trash} and the new one at {dst}. Nothing more was removed - "
            "tell me and we'll sort it out together."))

    link_note = ""
    if leave_link and not _is_junction(src):
        if os.path.exists(_lp(src)):
            # A program recreated the old folder the instant it went - Chrome
            # does this to Downloads. Empty, it is just in the way; with
            # anything in it, it is not mine to remove.
            try:
                os.rmdir(_lp(src))
            except OSError:
                return _finish(path, "failed", 4, (
                    f"Something new appeared at {src} while I was working, so I haven't made the link or removed "
                    f"anything. The original is safe, renamed to {trash}, and the checked copy is at {dst}. "
                    "Move what's new out of the way and say the move again to finish."))
        why = _make_junction(src, dst)
        if why:
            what = f"I couldn't make the link at the old path ({why})."
            if placed_now:
                return _back_out(path, src, trash, dst, staging, True, what)
            return _finish(path, "failed", 6, (
                f"{what} The old copy is still at {trash} and the checked copy at {dst}. Nothing was removed. "
                "Say the move again without a link if you'd rather."))
    if leave_link:
        link_note = " The old location still works, through a link."

    _update_state(path, state="cleaning")
    stayed = _delete_tree(trash)
    freed = record.get("bytes_total") or 0
    hashed, verified = record.get("hashed_files"), record.get("files_verified")
    total = record.get("files_total") or verified or left.files
    if hashed is not None and verified:
        by_sum = "every one by checksum" if hashed >= verified else f"{hashed} by checksum"
        checked = f"copied and checked {_count(total, 'file')} ({by_sum}, all by size and date)"
    else:
        checked = f"copied and checked {_count(total, 'file')} (by size and date)"
    # Said aloud, so drives and folder names - not C:\Users\...\Downloads,
    # which text-to-speech reads out character by character.
    where = f"{_drive_word(dst)}"
    if stayed:
        return _finish(path, "done", 0, (
            f"Moved {src.name} to {where}: {checked}. But {_count(len(stayed), 'item')} in the old copy couldn't "
            f"be removed, so some of it is still at {trash}.{link_note}"))
    return _finish(path, "done", 0, (
        f"Moved {src.name} to {where}: {checked}, then removed the old copy.{link_note} "
        f"About {_say_bytes(freed)} freed on {_drive_word(src)}."))


def _where_it_is(trash: Path, src: Path) -> str:
    """
    Put the renamed original back where it was and SAY what happened - which
    is not always "put it back": if something took its place, the original
    stays where it was renamed to, and claiming otherwise sends him looking
    for a folder that is not there.
    """
    try:
        os.rename(_lp(trash), _lp(src))
        return "I put the original back exactly as it was."
    except OSError:
        return f"The original is safe, still renamed to {trash}."


def _looks_finished(record: dict) -> bool:
    """
    Everything the job does is on the disk - the folder is at its new place, the
    renamed original and the staging folder are gone, and the old path is a link
    or gone - whatever the record says. (The process can be killed, or the drive
    full, between the last delete and the last line written to the record.)
    """
    try:
        src, dst = Path(str(record.get("src", ""))), Path(str(record.get("dst", "")))
        key = record.get("id") or move_key(src, dst)
        return (os.path.isdir(_lp(dst)) and not os.path.isdir(_lp(_trash_path(src, key)))
                and not os.path.isdir(_lp(_staging_path(dst, key)))
                and (not os.path.lexists(_lp(src)) or _is_junction(src)))
    except OSError:
        return False


def _what_is_left(record: dict) -> str:
    """What a cut-short move has left on the disk, read from the disk: which of the old copy and the new there is."""
    try:
        src, dst = Path(str(record.get("src", ""))), Path(str(record.get("dst", "")))
        key = record.get("id") or move_key(src, dst)
        trash, staging = _trash_path(src, key), _staging_path(dst, key)
        name = src.name or "the folder"
        ask = f"Say move my {name} to {_drive_word(dst).replace('the ', '').replace(' drive', '')}"
        if os.path.isdir(_lp(trash)):
            if os.path.isdir(_lp(staging)):
                return (f"The original is renamed aside ({trash.name}) and the copy on {_drive_word(dst)} is still "
                        f"being checked. Nothing has been removed. {ask} to finish it.")
            return (f"The checked copy is on {_drive_word(dst)}. The old copy is still on {_drive_word(src)} as "
                    f"{trash.name} - all of it, or part of it if the cleanup had started - and nothing in it was "
                    f"removed that isn't on the new drive. {ask} and I'll check what's left against the new copy "
                    "and finish removing it.")
        if os.path.isdir(_lp(staging)):
            return (f"The copy on {_drive_word(dst)} was partly built. The original is untouched. {ask} and I'll "
                    "pick up where it stopped.")
    except OSError:
        pass
    src_name = Path(str(record.get("src", ""))).name or "the folder"
    return (f"Nothing was removed. Say move my {src_name} to "
            f"{_drive_word(record.get('dst', '')).replace('the ', '').replace(' drive', '')} and I'll pick up where it stopped.")


# ------------------------------------------------------------------- plans
# The dry runs he has heard, by the folder and place they resolved to.
_PLANNED: dict[str, tuple[float, dict, frozenset[str]]] = {}
# ...and, for the question "Confirm?", by the words the move is called with.
_HEARD: dict[tuple[str, str], tuple[float, dict]] = {}


def _forget_plans() -> None:
    _PLANNED.clear()
    _HEARD.clear()


def _text_key(path: str, destination: str) -> tuple[str, str]:
    return (str(path).strip().lower(), str(destination).strip().lower())


def _remember_plan(plan: Plan, path: str, destination: str, leave_link: bool | None) -> None:
    assert plan.src is not None and plan.dst is not None
    now = time.monotonic()
    _PLANNED[move_key(plan.src, plan.dst)] = (
        now, {"path": path, "destination": destination, "leave_link": leave_link},
        frozenset(c.lower() for c in plan.carried))
    _HEARD[_text_key(path, destination)] = (now, {
        "src": str(plan.src), "dst": str(plan.dst),
        "redirected_from": str(plan.redirected_from) if plan.redirected_from else "",
        "same_volume": plan.same_volume, "default_link": plan.leave_link if leave_link is None else None,
        "finishing": plan.finishing, "carried": list(plan.carried),
    })


def _question_was_named(path: str, destination: str) -> bool:
    """A dry run is on file under THESE words, so the question asked for this call was worded from it."""
    entry = _HEARD.get(_text_key(path, destination))
    return entry is not None and time.monotonic() - entry[0] <= PLAN_TTL_S


def _forget_this_plan(plan: Plan) -> None:
    """The move has started: what he heard is spent, so a second yes cannot start it again, and the next question says so."""
    assert plan.src is not None and plan.dst is not None
    _PLANNED.pop(move_key(plan.src, plan.dst), None)
    for text_key, (_, heard) in list(_HEARD.items()):
        if heard["src"] == str(plan.src) and heard["dst"] == str(plan.dst):
            _HEARD.pop(text_key, None)


def _heard_carried(plan: Plan) -> frozenset[str] | None:
    """The protected files he was told about for this very move, or None if he has not heard a fresh dry run of it."""
    assert plan.src is not None and plan.dst is not None
    entry = _PLANNED.get(move_key(plan.src, plan.dst))
    if entry is None or time.monotonic() - entry[0] > PLAN_TTL_S:
        return None
    return entry[2]


def last_plan() -> dict | None:
    """The arguments of the dry run he most recently heard, or None once it has gone stale. The ONE place the TTL is enforced."""
    live = [(at, args) for at, args, _ in _PLANNED.values() if time.monotonic() - at <= PLAN_TTL_S]
    return max(live, key=lambda e: e[0])[1] if live else None


def _cut(text: str, n: int = 110) -> str:
    """Shorten a long path from the LEFT: its last folders are the part that says which one it is."""
    return text if len(text) <= n else "..." + text[-(n - 3):]


def confirmation_summary(args: dict) -> str | None:
    """
    What the question "Confirm?" is about, for move_folder: the REAL source
    folder, the REAL destination (the redirected one, if his was taken), whether
    a link is left, and every protected file the folder carries along - from the
    dry run he heard, by the same words. The first version asked about the
    words he had spoken ("move folder: downloads to d"), which named neither
    the fuzzy-matched folder nor the "Downloads from C" it was redirected to.

    Cheap and never raises: it reads what plan_folder_move remembered and does
    not touch the disk. With no fresh dry run behind it, it says so (move_folder
    itself will not start without one). None means "use the generic wording".
    """
    try:
        path, destination = args.get("path"), args.get("destination")
        if not (isinstance(path, str) and isinstance(destination, str) and path.strip() and destination.strip()):
            return None
        entry = _HEARD.get(_text_key(path, destination))
        if entry is None or time.monotonic() - entry[0] > PLAN_TTL_S:
            return f"move folder: {_cut(path)} to {_cut(destination)}. I haven't measured it yet"
        heard = entry[1]
        parts = [f"move folder: {_cut(heard['src'])} to {_cut(heard['dst'])}"]
        if heard["redirected_from"]:
            parts.append(f"a new folder, because {_cut(heard['redirected_from'])} is already there with other "
                         "things in it")
        if heard["finishing"]:
            parts.append("this finishes a move that was cut short")
        else:
            asked = args.get("leave_link")
            leave = heard["default_link"] if asked is None else bool(asked)
            if heard["same_volume"] and leave is not None and not leave:
                parts.append("it stays on the same drive and no link is left")
            else:
                parts.append("a link will stay at the old path" if leave else "no link will be left at the old path")
        if heard["carried"]:
            parts.append(_carried_sentence(heard["carried"]).rstrip("."))
        # Sentences, because it is read aloud; the template adds ". Confirm?".
        return parts[0] + "".join(". " + p[0].upper() + p[1:] for p in parts[1:])
    except Exception:  # noqa: BLE001 - the question must still be asked
        return None


# ------------------------------------------------------------------- tools
def plan_folder_move(path: str, destination: str, leave_link: bool | None = None) -> str:
    """
    What moving a folder to another drive would do - GREEN, changes nothing.

    Says the size, the file count, what it frees, where it ends up, and
    anything it would refuse. Run this and say it BEFORE move_folder.
    """
    from .. import taint

    plan = make_plan(path, destination, leave_link)
    if plan.refusal is None:
        # A dry run made on a turn that read a page is not remembered: it would
        # arm the router's "move it" with arguments a page chose. (It is also
        # refused at the gate; this is the same rule where the memory is kept.)
        if taint.origin_now() == "content":
            return describe_plan(plan)
        _remember_plan(plan, path, destination, leave_link)
        return describe_plan(plan) + (" Say go ahead and move it to finish." if plan.finishing
                                      else " Say go ahead and move it to start.")
    return describe_plan(plan)


def move_folder(path: str, destination: str, leave_link: bool | None = None) -> str:
    """
    Move a whole folder to another drive and check it arrived - RED.

    Needs a plan_folder_move for the same folder and destination from the last
    fifteen minutes; without one it only describes the move. Same-drive moves
    are an instant rename. Cross-drive moves run in the background.
    """
    from .. import taint

    try:
        plan = make_plan(path, destination, leave_link)
        if plan.refusal:
            return plan.refusal
        heard = _heard_carried(plan)
        if heard is None:
            return (describe_plan(plan) + " I needed to measure it first, so I haven't started - "
                    "say go ahead and move it once you've heard that.")
        unheard = [c for c in plan.carried if c.lower() not in heard]
        if unheard:
            if taint.origin_now() != "content":
                _remember_plan(plan, path, destination, leave_link)
            return (f"The protected files in that folder aren't the ones I told you about - "
                    f"{_names(unheard)} {'is' if len(unheard) == 1 else 'are'} new. I haven't started. "
                    f"{_carried_sentence(plan.carried)} Say go ahead and move it if you're happy with that.")
        if plan.carried and not _question_was_named(path, destination):
            # He heard the dry run, but the question he has just been asked was
            # worded from other words than the ones this call uses, so it could
            # not name the protected files. A named yes is the whole condition
            # for carrying them: ask again with the names in it.
            if taint.origin_now() != "content":
                _remember_plan(plan, path, destination, leave_link)
            return (f"{_carried_sentence(plan.carried)} I haven't started: the question you just answered didn't "
                    "name them. Say go ahead and move it, and I'll name them when I ask.")
        if plan.same_volume and not plan.finishing:
            return _rename_move(plan)
        return _start_job(plan)
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't start that move ({type(exc).__name__}: {exc}). Nothing was changed."


def _rename_move(plan: Plan) -> str:
    assert plan.src is not None and plan.dst is not None
    try:
        os.makedirs(_lp(plan.dst.parent), exist_ok=True)
        if os.path.lexists(_lp(plan.dst)):
            # An EMPTY folder of that name is not in the way (rename refuses to
            # land on it, so it goes first); anything in it is, and is not mine.
            if _is_occupied(plan.dst):
                return (f"There's already something at {plan.dst}, so I haven't moved {plan.src.name}. "
                        "Nothing was changed.")
            os.rmdir(_lp(plan.dst))
        os.rename(_lp(plan.src), _lp(plan.dst))
    except FileExistsError:
        return f"There's already a folder at {plan.dst}, so I haven't moved {plan.src.name}. Nothing was changed."
    except OSError as exc:
        return (f"Something is using {plan.src.name} ({str(exc).strip() or type(exc).__name__}), so I haven't "
                "moved it. Close whatever has it open and ask again.")
    note = ""
    if plan.leave_link:
        why = _make_junction(plan.src, plan.dst)
        note = (f" A link at {plan.src} still leads there." if not why
                else f" I couldn't leave a link at the old path ({why}).")
    _forget_this_plan(plan)
    return f"Moved {plan.src.name} to {plan.dst}. It was the same drive, so that was instant.{note}"


def _interpreter() -> str:
    """Jalen's own Python, windowless when there is one: the job has no console of its own to show."""
    venv = ROOT / ".venv" / "Scripts"
    for candidate in (venv / "pythonw.exe", Path(sys.executable).with_name("pythonw.exe")):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def summarise(log_text: str) -> str:
    """The sentence the announcer speaks: the job's own RESULT line."""
    for line in reversed((log_text or "").splitlines()):
        if line.startswith("RESULT: "):
            return line[len("RESULT: "):].strip()
    return ("it didn't say how it went before it stopped. Say move status to see where it got to; "
            "nothing is removed from the old place until a copy has been checked.")


def _start_job(plan: Plan) -> str:
    assert plan.src is not None and plan.dst is not None
    from . import devwork

    record_path = _new_state(plan)
    if record_path is None:
        return ("I couldn't write down the job - the drive its notes go on may be full - so I haven't started. "
                "Nothing was changed.")
    label = f"The move of {plan.src.name} to {_drive_word(plan.dst)}"
    reply = devwork.start_background_run(
        [_interpreter(), "-m", "jarvis.tools.foldermove", "run", str(record_path)],
        cwd=ROOT, label=label, summarise=summarise, timeout_s=JOB_TIMEOUT_S)
    if not str(reply).startswith("Running"):
        if plan.finishing or plan.resuming:
            # The record is what lets the leftovers be finished; keep it.
            _update_state(record_path, state="failed", result=str(reply))
        else:
            # No record at all: one left behind reads as a move that was cut short,
            # and the next plan would offer to "pick up" something that never began.
            try:
                record_path.unlink(missing_ok=True)
            except OSError:
                pass
        return str(reply)
    _forget_this_plan(plan)
    return (f"{'Finishing' if plan.finishing else 'Started moving'} {plan.src.name} to {plan.dst}: "
            f"{_count(plan.files, 'file')}, {_say_bytes(plan.bytes)}. "
            "It runs in the background and I'll tell you when it's finished and checked. Nothing is removed from "
            "the old place until every file has been verified on the new one. Say move status to check on it, or "
            "stop the background job to cancel it - either way it can pick up where it left off."
            + (f" {plan.interrupted_note}" if plan.interrupted_note else ""))


def folder_move_status() -> str:
    """Where each folder move is, and whether one was cut short - GREEN."""
    try:
        records = [r for r in (_read_state(p) for p in MOVES_DIR.glob("*.json")) if r]
    except OSError:
        records = []
    if not records:
        return "There are no folder moves on record."
    records.sort(key=lambda r: r.get("updated_at", 0), reverse=True)
    lines = []
    for r in records[:5]:
        name = Path(str(r.get("src", ""))).name or "a folder"
        where = _drive_word(r.get("dst", ""))
        state = _effective_state(r)
        if state == "running":
            lines.append(f"Moving {name} to {where}: {_progress_words(r)}.")
        elif state == "interrupted" and _looks_finished(r):
            lines.append(f"The move of {name} to {where} looks as if it finished: the folder is there and nothing "
                         "of the old copy is left. It was stopped just before it wrote that down.")
        elif state == "interrupted":
            lines.append(f"The move of {name} to {where} was interrupted at {_progress_words(r)}. "
                         + _what_is_left(r))
        elif state == "done":
            lines.append(r.get("result") or f"The move of {name} to {where} finished.")
        else:
            lines.append(r.get("result") or f"The move of {name} to {where} stopped ({state}).")
    return " ".join(lines)


# ---------------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - a stream that cannot be reconfigured is already fine
            pass
    if len(argv) == 2 and argv[0] == "run":
        return run_move(argv[1])
    _say("usage: python -m jarvis.tools.foldermove run <record.json>")
    return 2


REGISTRY: dict[str, Any] = {
    "plan_folder_move": plan_folder_move,
    "move_folder": move_folder,
    "folder_move_status": folder_move_status,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))


if __name__ == "__main__":
    raise SystemExit(main())
