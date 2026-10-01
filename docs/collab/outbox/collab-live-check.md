# OUTBOX — Session B (Collaborator), branch `collab/live-check` (Lane C1)

Single writer: Session B. Integrator: read, never edit.

```
BRANCH: collab/live-check   BASE: main 833adbb
COMMITS: 108e30f (the live check script), 9dc885c (jalen check left a live session alone), + this note
TESTS: 5,282 collected, 37 new. Focused: 31 + 6 new pass; neighbours (sticker, voice, premium post,
       every check_env test) 186 pass.
FULL GATE (worktree, once): 13 failed, 5,257 passed, 12 skipped in 8 min 17 s. The 13 are exactly the
       expected worktree failures in the brief (attachments 1, conversation M1 2, native host 2, CapCut 1,
       voice pipeline 5, wake 2). None is new.
```

## DONE
- **`scripts/live_telegram_check.py`** is the owner-run check that C1 asks for. It runs from the main folder with Jalen stopped.
    - It looks up 🚀 👏 📌 by character and by name, and lists his sets and packs (titles and counts).
    - It sends **one** premium-emoji post and **one** voice message to Saved Messages, with a typed yes before each, and reads both back.
    - It prints a timing table and a paste-back block. Those numbers replace stickers.py's `[NOT MEASURED]` values.
- **Safety.**
    - It takes **run.py's own single-instance lock** (mode `telegram-check`). It refuses while Jalen runs, and run.py refuses while it runs.
    - It refuses to run from a work copy.
    - Before each send, the name must resolve to his own account (`is_self`) through the same `_resolve` the send uses.
    - It prints no message, id, name, handle or phone number. Pack titles are stripped of control characters.
    - Ctrl+C, a stop request, or an offline Telegram all end with the table printed, the connection closed and the lock released.
- **Reviewed before commit** by two independent skeptics. Their two major findings, both fixed with tests:
    - A failed voice read-back would have been reported as "arrived". The failure sentence contains the success phrase.
    - An unreachable Telegram ended in a traceback that lost the measurements.
- **Mutation check.** I broke 22 of the script's guards one at a time, including a re-creation of the voice bug. A test failed every time.
- **`scripts/check_env.py`:** `jalen check` no longer opens the Telegram session while Jalen (or the live check) holds it.
    - Cause: `run.py --check` returns before the lock, so the diagnostic was a second Telethon client.
    - The 4 tests for this failed on main first. `test_check_names_every_account.py`'s fixture now points the lock at tmp_path.

## How the owner runs it (after this branch is merged — it refuses to run from a worktree)
```powershell
cd C:\Users\user\Desktop\Jarvis-setup\jarvis
.venv\Scripts\python.exe run.py --stop
.venv\Scripts\python.exe scripts\live_telegram_check.py
```
Answer `y` to the two sends, then paste back the block between `--- paste this back` and `--- end ---`. To check the reads only, with nothing sent, add `--no-send`.

## NOT DONE / UNVERIFIED
- **It has not run against his account.** That run is his. Nothing here has touched the real session.
- What the reviewers flagged but nobody verified: on an account without Premium, Saved Messages may keep custom emoji that a channel post would drop. The script says so instead of claiming the channel works.
- Ctrl+C during a Telegram step waits for that step to return, up to about a minute. Whether Python's lock wait can be interrupted on Windows was not measured.
- The lock does not stop the kill hotkey, `run.py --stop` or `jalen.ps1 restart` from killing the check (they kill whatever holds the lock after 8 s). The check stops cleanly if the request lands between steps, and the intro asks him not to use those.

## NEEDS FROM MAIN
Nothing in an in-flight file. Both commits touch only `scripts/` and `tests/`.

## RISKS / WHAT I WOULD CHECK NEXT
- After his run: replace `_MAX_SETS`, `_PARALLEL` and `_INDEX_TTL_S` in `stickers.py` with measured values. That file is telegram-fixes' while Wave 4 is open.
- **The full gate wrote under `data/`.** It left 55 speech-cache files in this worktree's `data\tts_cache`, all at one timestamp near the end. In the main checkout that would be the live cache. My two test files replace speech rendering (`messaging._speech_mp3` is patched), but which test writes the cache is not yet proven. Reported in collab-review-1.md; I am narrowing it down. The same worktree also got a `data\bridge.json` from a `Jalen()` test, which is the bridge finding in collab-browser `6a1dab2`.
