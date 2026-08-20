"""
His PERSONAL Telegram account, over MTProto (Telethon).

NOT THE SAME THING AS THE BOT
-----------------------------
integrations/telegram_bot.py is a *bot* — a separate identity people talk
TO, and the way he drives Jarvis from his phone. This module is his own
account: it reads the chats he actually has and can send as him. Two
different credentials, two different trust levels, deliberately two files.

    bot      TELEGRAM_BOT_TOKEN                  another identity
    personal TELEGRAM_API_ID + TELEGRAM_API_HASH  him

WHY THE API ID ALONE ISN'T A LOGIN
----------------------------------
api_id/api_hash say "this is an approved Telegram application". They do not
say "this is O'ktam". Signing in as him needs his phone number, the code
Telegram sends to his other devices, and his 2FA password if set — after
which Telethon stores a *session file* that stands in for all of it.

That session file is a complete, password-less login to his account. It is
guarded three ways: .gitignore excludes *.session, it lives under data/
which is git-ignored wholesale, and safety.yaml's never_touch patterns list
*.session so Jarvis's own file tools refuse to read or move it.

THREADING
---------
Telethon binds its client to the event loop that created it, and Jarvis
calls tools from asyncio.to_thread worker threads that each have no loop at
all. Creating a client per call would work but pays a full reconnect
(~1-2s) on every "read my telegram" — the exact kind of cost this project
just spent a round removing. So the client lives on one dedicated loop
thread for the process lifetime, and calls are submitted to it.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, Callable

from ..config import SECRETS

ROOT = Path(__file__).resolve().parent.parent.parent
SESSION_PATH = ROOT / "data" / "telegram_user"   # Telethon appends .session


class TelegramNotConnected(RuntimeError):
    """No authorised session. Carries what to do about it."""


def have_session() -> bool:
    return SESSION_PATH.with_suffix(".session").exists()


def credentials() -> tuple[int, str]:
    """api_id/api_hash from .env, validated. Never returns them anywhere."""
    raw_id = (SECRETS.telegram_api_id or "").strip()
    api_hash = (SECRETS.telegram_api_hash or "").strip()
    if not raw_id or not api_hash:
        raise TelegramNotConnected(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH are missing from .env. "
            "Get them from my.telegram.org — see CREDENTIALS.md."
        )
    try:
        api_id = int(raw_id)
    except ValueError as exc:
        raise TelegramNotConnected(
            "TELEGRAM_API_ID must be the numeric id from my.telegram.org."
        ) from exc
    return api_id, api_hash


def build_client(*, loop: asyncio.AbstractEventLoop | None = None):
    """A TelegramClient pointed at the stored session. Does not connect."""
    from telethon import TelegramClient

    api_id, api_hash = credentials()
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(str(SESSION_PATH), api_id, api_hash, loop=loop)


class _Runtime:
    """
    One Telethon client on one dedicated loop thread.

    Started lazily: constructing this at import time would make merely
    importing jarvis.tools open a network connection, which breaks the test
    suite and slows startup for people who never touch Telegram.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client = None
        self._lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None:
            return self._loop
        ready = threading.Event()

        def runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            loop.run_forever()

        self._thread = threading.Thread(
            target=runner, name="jarvis-telegram", daemon=True
        )
        self._thread.start()
        ready.wait(timeout=10)
        if self._loop is None:
            raise TelegramNotConnected("Couldn't start the Telegram loop thread.")
        return self._loop

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not have_session():
            raise TelegramNotConnected(
                "Your personal Telegram isn't signed in yet. Run: "
                ".venv\\Scripts\\python.exe scripts\\connect_telegram.py"
            )
        loop = self._ensure_loop()
        client = build_client(loop=loop)

        async def _connect():
            await client.connect()
            if not await client.is_user_authorized():
                raise TelegramNotConnected(
                    "The stored Telegram session is no longer authorised — "
                    "it may have been revoked from another device. Run: "
                    ".venv\\Scripts\\python.exe scripts\\connect_telegram.py"
                )
            return client

        self._client = asyncio.run_coroutine_threadsafe(_connect(), loop).result(60)
        return self._client

    def run(self, work: Callable[[Any], Any], timeout: float = 60.0):
        """Submit `work(client)` to the Telegram loop and block for it."""
        with self._lock:
            client = self._ensure_client()
            loop = self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(work(client), loop).result(timeout)

    def shutdown(self) -> None:
        if self._client is not None and self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._client.disconnect(), self._loop
                ).result(10)
            except Exception:
                pass
        self._client = None
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop = None


RUNTIME = _Runtime()
