r"""
Where the disk went, classified by whether it is safe to delete.

    .\jalen.ps1 disk

The drive hit 0.00 GB during this build and a test run died with
`OSError: [Errno 28]`. Jalen already has `disk_report` and
`cleanup_suggestions`; this is the version for the person doing the
cleaning, and it exists because those two answer "what is big" rather than
"what may I delete".

THE CLASSIFICATION IS THE POINT, and it never deletes anything itself:

    SAFE TO REGENERATE     caches and build artefacts. Deleting them costs
                           time, never data. Python rebuilds __pycache__;
                           pip re-downloads wheels.
    SAFE WITH CONFIRMATION real files that are probably finished with -
                           logs, old downloads. He decides.
    DO NOT TOUCH           anything irreplaceable, and everything the
                           assistant itself needs to keep working.

Nothing here removes a file. It measures, sorts, and prints the command HE
would run. An assistant that tidies a disk on its own initiative is one bad
classification away from deleting a project.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOME = Path.home()

# Never counted as a cleanup candidate, whatever their size. Losing any of
# these costs something that cannot be re-downloaded.
PROTECTED = (
    ROOT / "data" / "vault.json",
    ROOT / "data" / "audit.jsonl",
    ROOT / "data" / "audit.db",
    ROOT / "data" / "telegram_user.session",
    ROOT / "data" / "google_token.json",
    ROOT / ".env",
    ROOT / "config",
    ROOT / "models",
    ROOT / "data" / "wake_training",
)


def folder_size(path: Path, limit: int = 400_000) -> tuple[int, int]:
    """(bytes, files). Bounded, so a huge tree cannot hang the report."""
    total = files = 0
    try:
        for root, _dirs, names in os.walk(path):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                    files += 1
                except OSError:
                    pass
                if files > limit:
                    return total, files
    except OSError:
        pass
    return total, files


def gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    total, used, free = shutil.disk_usage(str(ROOT))
    print()
    print(f"  Disk: {gb(free)} free of {gb(total)}")
    if free < 1e9:
        print("  THIS WILL BREAK THINGS. Under 1 GB the audit log, speech")
        print("  synthesis and transcription all fail with their own")
        print("  confusing errors.")
    print()

    regenerate: list[tuple[str, int, str]] = []
    confirm: list[tuple[str, int, str]] = []

    # --- caches, inside the project -----------------------------------------
    cache_bytes = cache_dirs = 0
    for root, dirs, _names in os.walk(ROOT):
        for name in list(dirs):
            if name in ("__pycache__", ".pytest_cache"):
                size, _ = folder_size(Path(root) / name)
                cache_bytes += size
                cache_dirs += 1
                dirs.remove(name)
    if cache_bytes:
        regenerate.append((
            f"Python caches ({cache_dirs} folders)", cache_bytes,
            "Python rebuilds these automatically on the next import.",
        ))

    # --- pip's download cache -----------------------------------------------
    pip_cache = HOME / "AppData" / "Local" / "pip" / "cache"
    size, _ = folder_size(pip_cache)
    if size:
        regenerate.append((
            "pip download cache", size,
            r".venv\Scripts\python.exe -m pip cache purge",
        ))

    # --- temp ---------------------------------------------------------------
    for label, path in (("Windows user temp", HOME / "AppData" / "Local" / "Temp"),
                        ("Windows system temp", Path("C:/Windows/Temp"))):
        size, count = folder_size(path)
        if size:
            confirm.append((
                f"{label} ({count} files)", size,
                'say "Jalen, clear the temp files" - it asks first',
            ))

    # --- our own generated output -------------------------------------------
    for label, path, how in (
        ("captured coding-agent output", ROOT / "data" / "coding_jobs",
         "logs from finished background jobs"),
        ("crash log", ROOT / "data" / "crash.log",
         "keep it if anything has crashed recently"),
        ("router misses", ROOT / "data" / "router_misses.log",
         "used to decide which phrases deserve a rule - worth keeping"),
    ):
        size, _ = folder_size(path) if path.is_dir() else (
            (path.stat().st_size, 1) if path.exists() else (0, 0)
        )
        if size > 1e6:
            confirm.append((label, size, how))

    # --- the big folders on his Desktop, for context ------------------------
    desktop_items = []
    desktop = HOME / "Desktop"
    if desktop.is_dir():
        for entry in desktop.iterdir():
            try:
                size = (entry.stat().st_size if entry.is_file()
                        else folder_size(entry)[0])
            except OSError:
                continue
            if size > 2e8:
                desktop_items.append((entry.name, size))
    desktop_items.sort(key=lambda kv: -kv[1])

    def section(title, rows, note):
        if not rows:
            return
        print(f"  {title}")
        print(f"  {note}")
        print()
        for label, size, how in sorted(rows, key=lambda r: -r[1]):
            print(f"    {gb(size):>9s}  {label}")
            print(f"               {how}")
        print(f"    {gb(sum(r[1] for r in rows)):>9s}  TOTAL")
        print()

    section("SAFE TO REGENERATE", regenerate,
            "Deleting these costs time, never data.")
    section("SAFE WITH CONFIRMATION", confirm,
            "Real files, probably finished with. Your call.")

    if desktop_items:
        print("  DO NOT TOUCH - your own files, listed for context only")
        print("  These are the big things on your Desktop. Nothing here")
        print("  should be deleted by an assistant.")
        print()
        for name, size in desktop_items[:12]:
            print(f"    {gb(size):>9s}  {name}")
        print()

    print("  PROTECTED - never offered for deletion at any size:")
    for path in PROTECTED:
        mark = "exists" if path.exists() else "not created yet"
        print(f"    {path.relative_to(ROOT) if ROOT in path.parents or path == ROOT else path}  ({mark})")
    print()
    print("  Nothing was deleted. This report only measures and sorts.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
