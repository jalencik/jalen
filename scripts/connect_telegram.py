"""
Sign Jalen in to your PERSONAL Telegram account. Run once.

    .venv\\Scripts\\python.exe scripts\\connect_telegram.py

This is NOT the bot. The bot is a separate identity you send messages TO.
This signs in as YOU, so Jalen can read your real chats and send as you.

You will be asked for:
  1. your phone number, in full international form (+998...)
  2. the login code Telegram sends to your other Telegram devices
  3. your two-step verification password, if you have one set

None of those are stored. What IS stored is a session file at
data/telegram_user.session, which is a complete password-less login to your
account — treat it exactly like a password. It is protected three ways:
.gitignore excludes *.session, data/ is git-ignored wholesale, and
safety.yaml's never_touch patterns stop Jalen's own file tools reading it.

    --status   check the current session without changing anything
    --logout   sign out and delete the session

Stop Jalen first: only one process may use the session file at a time, and
that includes --status. This script takes Jalen's own single-instance lock
(mode "telegram-connect"), so it refuses while Jalen or the live Telegram
check is running, and Jalen refuses to start while this runs.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.integrations import telegram_user  # noqa: E402

BAR = "=" * 68

# How run.py --status names this process while it holds the lock.
LOCK_MODE = "telegram-connect"
CONNECT_COMMAND = ".venv\\Scripts\\python.exe scripts\\connect_telegram.py"
STOP_COMMAND = ".venv\\Scripts\\python.exe run.py --stop"
# Two-step verification password attempts before giving up. Telegram itself
# rate-limits wrong passwords; three is the same allowance as the login code.
PASSWORD_ATTEMPTS = 3


async def _status() -> int:
    if not telegram_user.have_session():
        print("Not signed in. Sign in with:")
        print(f"    {CONNECT_COMMAND}")
        return 1
    client = telegram_user.build_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print("A session file exists but Telegram no longer accepts it - it was")
            print("signed out, probably from Telegram > Settings > Devices. Sign in again:")
            print(f"    {CONNECT_COMMAND}")
            return 1
        me = await client.get_me()
        name = " ".join(x for x in (me.first_name, me.last_name) if x)
        handle = f"@{me.username}" if me.username else str(me.id)
        print(f"Signed in as {name} ({handle})")
        print(f"Session: {telegram_user.SESSION_PATH.with_suffix('.session')}")
        return 0
    finally:
        await client.disconnect()


async def _logout() -> int:
    path = telegram_user.SESSION_PATH.with_suffix(".session")
    if not path.exists():
        print("There was no session to remove.")
        return 0
    client = telegram_user.build_client()
    await client.connect()
    try:
        if await client.is_user_authorized():
            await client.log_out()   # revokes it server-side, not just locally
            print("Signed out of Telegram.")
    finally:
        await client.disconnect()
    if path.exists():
        path.unlink()
        print(f"Deleted {path}")
    return 0


async def _connect() -> int:
    from telethon.errors import (
        FloodWaitError,
        PasswordHashInvalidError,
        PhoneCodeExpiredError,
        PhoneCodeInvalidError,
        PhoneNumberInvalidError,
        SessionPasswordNeededError,
    )

    print(BAR)
    print("  Signing Jalen in to your PERSONAL Telegram account")
    print(BAR)
    print()
    print("  This is not the bot. This is you.")
    print("  Telegram will send a login code to your other Telegram devices.")
    print()

    if telegram_user.have_session():
        print("  A session already exists.")
        answer = input("  Sign in again anyway? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("  Left as it was.")
            return 0

    try:
        telegram_user.credentials()
    except telegram_user.TelegramNotConnected as exc:
        print(f"  {exc}")
        return 1

    client = telegram_user.build_client()
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"  Already authorised as {me.first_name}.")
            return 0

        phone = input("  Phone number (e.g. +998901234567): ").strip()
        if not phone.startswith("+"):
            print("  It needs the country code, starting with +.")
            return 1

        try:
            await client.send_code_request(phone)
        except PhoneNumberInvalidError:
            print("  Telegram did not accept that number. Use the full international")
            print("  form with the country code, e.g. +998901234567.")
            return 1
        except FloodWaitError as exc:
            print(f"  Telegram asks to wait {getattr(exc, 'seconds', 0)} seconds before")
            print("  another login code. Run this again after that.")
            return 1
        print()
        print("  Telegram has sent a code to your other Telegram devices.")
        print("  (Check the Telegram app, not SMS, if you have it installed.)")
        print()

        for attempt in range(3):
            code = input("  Login code: ").strip()
            try:
                await client.sign_in(phone, code)
                break
            except PhoneCodeInvalidError:
                left = 2 - attempt
                print(f"  That code wasn't right. {left} attempt(s) left." if left
                      else "  That code wasn't right.")
                if not left:
                    return 1
            except PhoneCodeExpiredError:
                print("  That code expired. Run the script again for a new one.")
                return 1
            except SessionPasswordNeededError:
                # Two-step verification. getpass so it never appears on screen
                # or in the terminal's scrollback.
                import getpass

                print()
                print("  You have two-step verification on.")
                for left in range(PASSWORD_ATTEMPTS - 1, -1, -1):
                    password = getpass.getpass("  Telegram password (hidden): ")
                    try:
                        await client.sign_in(password=password)
                        break
                    except PasswordHashInvalidError:
                        if not left:
                            print("  That password wasn't right. Run the script again.")
                            return 1
                        print(f"  That password wasn't right. {left} attempt(s) left.")
                    finally:
                        # Not kept a moment longer than the call that needs it.
                        password = None
                break

        me = await client.get_me()
        name = " ".join(x for x in (me.first_name, me.last_name) if x)
        print()
        print(BAR)
        print(f"  Signed in as {name}")
        print(f"  Session stored at {telegram_user.SESSION_PATH.with_suffix('.session')}")
        print(BAR)
        print()
        print("  That file is a full login to your account. It is git-ignored")
        print("  and Jalen's own file tools are blocked from reading it.")
        print("  To revoke: this script with --logout, or Telegram >")
        print("  Settings > Devices > terminate session.")
        print()
        print("  Next: start Jalen (.\\jalen.ps1 start) - he picks the session up.")
        return 0
    finally:
        await client.disconnect()


def main(argv: list[str]) -> int:
    arg = argv[0] if argv else ""
    # Any other argument falls through to _connect(), so without this line
    # "--help" started a real Telegram sign-in instead of printing help.
    if arg in ("-h", "--help"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
        print(__doc__)
        return 0

    # ONE TELEGRAM CLIENT AT A TIME, ENFORCED. The docstring always said
    # "stop Jalen first", and nothing checked: run while Jalen was up, this
    # opened a second Telethon client on the same session file - the
    # collision that gets a session thrown out by Telegram. It now takes the
    # same lock run.py and scripts/live_telegram_check.py take.
    from jarvis import runtime

    already = runtime.acquire(LOCK_MODE)
    if already is not None:
        if already.mode == LOCK_MODE:
            print(f"Another Telegram sign-in is already running (pid {already.pid}). "
                  "Finish it first.")
        else:
            print(f"Jalen is running (pid {already.pid}, {already.mode} mode), and only one "
                  "program may use the Telegram session at a time. Stop him first:")
            print(f"    {STOP_COMMAND}")
            print("then run this again.")
        return 2
    try:
        if arg == "--status":
            return asyncio.run(_status())
        if arg == "--logout":
            return asyncio.run(_logout())
        return asyncio.run(_connect())
    except KeyboardInterrupt:
        print()
        print("Stopped. Nothing was changed unless it said 'Signed in'.")
        return 130
    finally:
        runtime.release()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
