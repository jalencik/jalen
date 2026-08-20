"""
Sign Jarvis in to your PERSONAL Telegram account. Run once.

    .venv\\Scripts\\python.exe scripts\\connect_telegram.py

This is NOT the bot. The bot is a separate identity you send messages TO.
This signs in as YOU, so Jarvis can read your real chats and send as you.

You will be asked for:
  1. your phone number, in full international form (+998...)
  2. the login code Telegram sends to your other Telegram devices
  3. your two-step verification password, if you have one set

None of those are stored. What IS stored is a session file at
data/telegram_user.session, which is a complete password-less login to your
account — treat it exactly like a password. It is protected three ways:
.gitignore excludes *.session, data/ is git-ignored wholesale, and
safety.yaml's never_touch patterns stop Jarvis's own file tools reading it.

    --status   check the current session without changing anything
    --logout   sign out and delete the session
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.integrations import telegram_user  # noqa: E402

BAR = "=" * 68


async def _status() -> int:
    if not telegram_user.have_session():
        print("Not signed in. Run this script with no arguments to sign in.")
        return 1
    client = telegram_user.build_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print("A session file exists but is no longer authorised.")
            print("It was probably revoked from another device. Sign in again.")
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
        PhoneCodeExpiredError,
        PhoneCodeInvalidError,
        SessionPasswordNeededError,
    )

    print(BAR)
    print("  Signing Jarvis in to your PERSONAL Telegram account")
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

        await client.send_code_request(phone)
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
                password = getpass.getpass("  Telegram password (hidden): ")
                await client.sign_in(password=password)
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
        print("  and Jarvis's own file tools are blocked from reading it.")
        print("  To revoke: this script with --logout, or Telegram >")
        print("  Settings > Devices > terminate session.")
        return 0
    finally:
        await client.disconnect()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--status":
        raise SystemExit(asyncio.run(_status()))
    if arg == "--logout":
        raise SystemExit(asyncio.run(_logout()))
    raise SystemExit(asyncio.run(_connect()))
