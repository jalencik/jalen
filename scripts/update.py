r"""
Update to the newest version, safely. (HANDOFF item 6, "no update path")

    .venv\Scripts\python.exe scripts\update.py
    .venv\Scripts\python.exe scripts\update.py --check    (look, don't touch)

WHAT AN UPDATE HAS TO GET RIGHT
-------------------------------
Almost nothing here is about fetching code. It is about not destroying a
working machine, because everything that makes this assistant HIS is
untracked and irreplaceable:

    data/vault.json              cannot be recovered; the passphrase is
                                 nowhere and by design
    data/telegram_user.session   a password-less login to his account
    data/google_token.json       an OAuth refresh token
    config/user.yaml             his settings
    .env                         every API key
    models/hey_jalen.onnx        a trained model, ~40 minutes to rebuild
    data/audit.jsonl             the diagnostic instrument this whole
                                 project has been debugged with

So the order is: refuse if the tree is dirty, back those up, pull, install,
run the tests, and say plainly what happened. If the tests fail afterwards
it says so and tells him how to go back — it does not roll back by itself,
because an automatic rollback that goes wrong leaves a state nobody chose.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
BACKUP_ROOT = ROOT / "data" / "backups"

TICK, CROSS, DASH = "  [ok] ", "  [XX] ", "  [--] "

# Irreplaceable, untracked, and each one costs something real to lose.
PRECIOUS = [
    ".env",
    "config/user.yaml",
    "data/vault.json",
    "data/google_token.json",
    "data/telegram_user.session",
    "data/site_approvals.json",
    # Which site each secret belongs to. Lost, every secret is untied and
    # he is asked about each one again - safe, but his answers were real.
    "data/secret_sites.json",
    "models/hey_jalen.onnx",
]


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True, check=check
    )


def say(text: str = "") -> None:
    print(text)


def is_a_git_checkout() -> bool:
    return (ROOT / ".git").exists() and git("rev-parse", "--git-dir").returncode == 0


def working_tree_is_clean() -> tuple[bool, str]:
    result = git("status", "--porcelain")
    return not result.stdout.strip(), result.stdout.strip()


def current_commit() -> str:
    return git("rev-parse", "--short", "HEAD").stdout.strip() or "unknown"


def back_up() -> Path:
    """
    Copy the irreplaceable files somewhere dated, before touching anything.

    Copy, not move, and never delete: this runs before an operation that
    might fail, so the originals must still be in place if it does.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_ROOT / stamp
    target.mkdir(parents=True, exist_ok=True)
    saved = 0
    for relative in PRECIOUS:
        source = ROOT / relative
        if not source.exists():
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        saved += 1
    say(f"{TICK}backed up {saved} irreplaceable files to {target.relative_to(ROOT)}")
    return target


def run_tests() -> bool:
    interpreter = str(PY) if PY.exists() else sys.executable
    say("\n  running the test suite (about two minutes)…\n")
    result = subprocess.run(
        [
            interpreter, "-m", "pytest", "tests/", "-q",
            "--ignore=tests/benchmark_latency.py",
            "--ignore=tests/benchmark_open.py",
            "--ignore=tests/benchmark_phrasing.py",
            # Live network. It fails on a flaky connection and that is not a
            # regression — the same caveat the handoff carries.
            "--ignore=tests/test_voice_pipeline.py",
        ],
        cwd=str(ROOT),
    )
    return result.returncode == 0


def check_only() -> int:
    say("\n  Checking for updates (nothing will be changed).\n")
    if not is_a_git_checkout():
        say(f"{DASH}not a git checkout — nothing to update from.")
        return 0

    say(f"{TICK}on commit {current_commit()}")
    fetch = git("fetch", "--quiet")
    if fetch.returncode != 0:
        say(f"{CROSS}couldn't reach the remote: {fetch.stderr.strip()}")
        return 1

    behind = git("rev-list", "--count", "HEAD..@{u}").stdout.strip()
    ahead = git("rev-list", "--count", "@{u}..HEAD").stdout.strip()
    if behind and behind != "0":
        say(f"{DASH}{behind} new commit(s) available.")
        log = git("log", "--oneline", "HEAD..@{u}").stdout.strip()
        for line in log.splitlines()[:15]:
            say(f"        {line}")
    else:
        say(f"{TICK}already up to date.")
    if ahead and ahead != "0":
        say(f"{DASH}{ahead} local commit(s) not pushed.")

    clean, dirty = working_tree_is_clean()
    if not clean:
        say(f"{DASH}uncommitted changes present:")
        for line in dirty.splitlines()[:10]:
            say(f"        {line}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Update Jalen safely.")
    parser.add_argument("--check", action="store_true",
                        help="report what an update would do, and change nothing")
    parser.add_argument("--skip-tests", action="store_true",
                        help="do not run the suite afterwards (not recommended)")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if args.check:
        return check_only()

    say("\n  Updating Jalen.\n")

    if not is_a_git_checkout():
        say(f"{CROSS}this is not a git checkout, so there is nothing to pull.")
        say("       Re-download the project, then copy your data/ and .env across.")
        return 1

    # Refuse rather than stash. A stash is a place uncommitted work goes to
    # be forgotten, and the whole point of this script is to not lose things.
    clean, dirty = working_tree_is_clean()
    if not clean:
        say(f"{CROSS}you have uncommitted changes. Deal with them first:\n")
        for line in dirty.splitlines()[:20]:
            say(f"        {line}")
        say("\n       commit them:   git add -A && git commit -m 'my changes'")
        say("       or discard:    git restore .")
        say("\n       Not stashing them automatically: a stash is where work")
        say("       goes to be forgotten, and losing things is the one thing")
        say("       this script exists to prevent.")
        return 1

    before = current_commit()
    backup = back_up()

    say("\n  pulling…")
    pull = git("pull", "--ff-only")
    say(pull.stdout.strip() or pull.stderr.strip())
    if pull.returncode != 0:
        say(f"\n{CROSS}pull failed. Nothing else was changed; you are still on {before}.")
        return 1

    after = current_commit()
    if after == before:
        say(f"\n{TICK}already up to date ({before}).")
        return 0
    say(f"{TICK}{before} -> {after}")

    say("\n  installing dependencies…")
    interpreter = str(PY) if PY.exists() else sys.executable
    install = subprocess.run(
        [interpreter, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
        cwd=str(ROOT),
    )
    if install.returncode != 0:
        say(f"{CROSS}dependency install failed. Fix that before starting Jalen.")
        return 1
    say(f"{TICK}dependencies up to date")

    if args.skip_tests:
        say(f"\n{DASH}tests skipped at your request.")
    elif run_tests():
        say(f"\n{TICK}all tests pass on the new version.")
    else:
        say(f"\n{CROSS}TESTS FAIL on the new version.")
        say(f"       Your files are backed up at {backup.relative_to(ROOT)}.")
        say(f"       To go back:  git reset --hard {before}")
        say("\n       Not rolling back automatically — an automatic rollback")
        say("       that goes wrong leaves a state nobody chose.")
        return 1

    say("\n  Check it over:  .\\jalen.ps1 check")
    say("  Then start it:  .\\jalen.ps1\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  Stopped.")
        sys.exit(1)
