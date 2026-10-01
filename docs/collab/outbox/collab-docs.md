# OUTBOX — Session B (Collaborator), branch `collab/docs` (Lane C3)

Single writer: Session B. Integrator: read, never edit.

```
BRANCH: collab/docs   BASE: main 833adbb   TESTS: 5,245 collected, 0 new (documents only, plus --help guards)
COMMITS (one per document):
  6212e38 SETUP.md      fc8980a README.md     fb7a1af CREDENTIALS.md   bd00e3e docs/CHROME_EXTENSION_SETUP.md
  abebb25 ARCHITECTURE  4873acc HANDOFF.md    b0b51e2 WHAT_JALEN_CAN_DO.md (regenerated)
  04c9e4b --help guards in 4 scripts          c3144d0 SETUP.md fixes from the fresh-laptop walk
```

## How it was done
- **Audit.** One auditor per document checked every command, path, count and sign-in step against the code. They found 106 wrong claims and 31 gaps:

  | Document | Wrong claims | Gaps |
  |---|---|---|
  | SETUP | 27 | 7 |
  | README | 17 | 6 |
  | CREDENTIALS + extension guide | 25 | 11 |
  | ARCHITECTURE + HANDOFF | 37 | 7 |

- **Write.** One writer applied the audits: one commit per document, each with a subject naming what was wrong for a reader.
- **Walk.** An independent "fresh laptop" reviewer then followed SETUP.md step by step against the repo. Verdict: *followable with fixes*, 12 points. Session B fixed them (`c3144d0`) and checked the facts behind them:
    - `channel_handle` and `DEFAULT_HANDLE` in `voice.py:116`
    - the `onboard.py` brain-step message
    - the `.gitignore` entries that `git archive` relies on

## The fix that matters most
**SETUP.md Step 2 said "copy the whole project folder".** That folder holds `.env` (his keys and Claude sign-in), `data\` (vault, Telegram and Google logins, audit log), `client_secret.json` and `config\user.yaml`. It now says to bring the code with `git archive` (tracked files only), plus `models\hey_jalen.onnx` by hand.

## What each document now is
- **SETUP.md**: 16 ordered steps, from a fresh Windows laptop to a full `check`, with three "one laptop" rules up front:
    - one Jalen at a time;
    - one program on the Telegram session;
    - always the same folder.
- **README.md**: what Jalen is today, the `jalen.ps1` commands, the test command and its known failure. It no longer claims 102 tools, 1,545 tests, an 88% router, hand gestures or "reload config".
- **CREDENTIALS.md**: every `.env` name and how to get it (never values). The Claude sign-in is the bundled `claude.exe setup-token` into `CLAUDE_CODE_OAUTH_TOKEN`, and `ANTHROPIC_API_KEY` stays empty.
- **docs/CHROME_EXTENSION_SETUP.md**: only what the app does through the extension today (four ext_ tools). The id is pinned, and Jalen must run from the folder the bridge was registered from.
- **ARCHITECTURE.md**: the current one-turn flow and module map. The August plan (Gemini offload, Haiku/Opus routing, FTS5) is now marked as history.
- **HANDOFF.md**: marked as the 23 August handoff, not today's state. The contributor rules CLAUDE.md cites stay at the same line numbers (79-111, 797-816).
- **WHAT_JALEN_CAN_DO.md**: regenerated with `scripts\capabilities.py`. 183 entries at 833adbb.
- **Script `--help`.** `connect_google.py`, `connect_telegram.py` and `vault_setup.py` used to *start* a Google consent, a Telegram sign-in or a passphrase prompt when asked for `--help`. `install_extension.py` refused `--help` as an extension id. Each now prints its docstring and exits 0. Every other argument behaves as before. Session B read the diff.

## NOT DONE / UNVERIFIED
- **Nobody has followed SETUP.md on a real fresh laptop.** The walk was done on paper against the repo, and no installer or sign-in was run.
- Counts are tied to `833adbb`: tools, tiers, router rules, test files, and 5,245 tests. **After Wave 4 merges, re-run `scripts\capabilities.py`** to regenerate WHAT_JALEN_CAN_DO.md, and refresh the counts in README and ARCHITECTURE.
- Branch overlap: `04c9e4b` touches `scripts/vault_setup.py`, which my C5 branch (`collab/vault`) may also touch. If both change it, merge the `--help` guard by hand; it is 9 lines at the top of `main`.

## For the Integrator (outside Lane C3)
- **`.env.example`** comments are older than the guide. They describe Gemini as "free-tier offload for cheap questions", which is not read by any code, and say to run plain `claude setup-token`, which doesn't exist on a fresh laptop. SETUP.md now says to follow the guide where they differ.
- **`scripts/install_autostart.py:117`** still prints "Ctrl+Alt+Space stops him"; the key is Ctrl+Alt+K. SETUP Step 15 warns the reader.
- **`check_env.py`** counts a missing Google login as blocking even when Gmail and Calendar are not wanted. SETUP says it is safe to ignore then. A `check` that reads `integrations.gmail.enabled` would remove the false alarm.
- **`personal.channel_handle`** falls back to the owner's own handle (`voice.py:116 DEFAULT_HANDLE`). That is right for him, and a second user's posts would carry it unless they set the key. SETUP now says so.
