"""
Telegram bot control (Phase D, spec C22/C24, H62).

The bot is a second front door onto the exact same Jalen: it drives
process() the same way run.py --text does, so every message gets the same
router, the same brain, the same safety gate — no separate code path to
keep in sync with the others (handoff §7: "prove there is no fourth path
around them").

Security: TELEGRAM_ALLOWED_USER_IDS is not a convenience filter — it's the
entire reason this is safe to expose full desktop control through
(jalen.yaml's own comment: empty list = bot refuses everyone, the safe
default). A message from anyone else is silently dropped, not answered
with a rejection — a bot that visibly responds to unauthorized senders
confirms it's live and listening, which is exactly the information a
security control shouldn't hand out for free.
"""
from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import Any

from .. import tools as jalen_tools


def is_authorized(user_id: int | None, allowed_user_ids: list[int]) -> bool:
    """
    The entire security boundary for this integration, pulled out as its
    own pure function so it's directly testable without fighting aiogram's
    Message model. An empty allow-list refuses everyone — the safe default
    (jalen.yaml's own comment) — and a message with no attributable user
    (user_id=None) is never authorized, regardless of the list's contents.
    """
    if not allowed_user_ids:
        return False
    return user_id is not None and user_id in allowed_user_ids


def prompt_keyboard(kind: str, prompt_id: str):
    """
    The buttons under a question: Yes and No under a "Confirm?", Stop under an
    announcement ("Say stop if you don't want that").

    The callback data is "jalen:<question id>:<choice>" - 18 characters, far
    inside Telegram's 64 bytes. The id is what makes a button belong to ONE
    question: Jalen.answer_from_tap refuses any tap whose id is not the open
    one, so an old keyboard left in the chat cannot answer a new question.
    aiogram is imported here and not at module level, like every heavy import.
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    if kind == "confirm":
        row = [InlineKeyboardButton(text="Yes", callback_data=f"jalen:{prompt_id}:yes"),
               InlineKeyboardButton(text="No", callback_data=f"jalen:{prompt_id}:no")]
    elif kind == "stop":
        row = [InlineKeyboardButton(text="Stop", callback_data=f"jalen:{prompt_id}:stop")]
    else:
        raise ValueError(f"no buttons for a question of kind {kind!r}")
    return InlineKeyboardMarkup(inline_keyboard=[row])


def build_dispatcher(jalen: Any, allowed_user_ids: list[int]):
    """
    Wires an aiogram Dispatcher to drive the given Jalen instance.
    jalen.say / jalen.say_blocking are overridden per message to send a
    Telegram reply to that chat — the same pattern run.py --text already
    uses to print instead of speak. Like --text mode, this is a
    single-consumer model: running the bot and voice mode at once isn't
    supported yet (see the module docstring in run.py's --telegram branch
    for what that would actually take).

    A question that needs an answer (Jalen.confirm, Jalen.announce) goes out
    with buttons - jalen._offer_buttons is installed the same way - and a tap
    comes back through handle_tap below, under the same allow-list as a message.
    """
    from aiogram import Dispatcher

    dp = Dispatcher()

    @dp.message()
    async def handle_message(message) -> None:  # noqa: ANN001
        user_id = message.from_user.id if message.from_user else None
        if not is_authorized(user_id, allowed_user_ids):
            return  # silent refusal by design — see module docstring

        text = (message.text or "").strip()
        if not text:
            return

        loop = asyncio.get_running_loop()  # aiogram's own loop, captured per message

        async def _send(t: str) -> None:
            await message.answer(t)

        # message.answer() is `def`, not `async def` — it returns aiogram's
        # own awaitable Request wrapper, not a native coroutine object.
        # run_coroutine_threadsafe requires the latter (asyncio.iscoroutine()
        # rejects anything else) and raised "TypeError: A coroutine object is
        # required" on every real reply until this was wrapped in an actual
        # async def. Found live: the very first real Telegram messages sent
        # to the bot all hit this — the safety gate itself worked (process()
        # ran, replies were computed), but no reply ever reached the chat.
        def say(t: str, force: bool = False) -> None:
            if not t:
                return
            asyncio.run_coroutine_threadsafe(_send(t), loop)

        def say_blocking(t: str) -> None:
            if not t:
                return
            future = asyncio.run_coroutine_threadsafe(_send(t), loop)
            future.result(timeout=15)

        jalen.say = say
        jalen.say_blocking = say_blocking

        # A question that wants an answer is sent with buttons. Like say(),
        # bound to THIS message's chat. If it raises, Jalen._put_the_question
        # falls back to say_blocking, so a keyboard that fails to send costs
        # the buttons and nothing else.
        async def _send_with_buttons(t: str, markup) -> None:  # noqa: ANN001
            await message.answer(t, reply_markup=markup)

        def offer_buttons(question: str, kind: str, prompt_id: str) -> None:
            markup = prompt_keyboard(kind, prompt_id)
            future = asyncio.run_coroutine_threadsafe(
                _send_with_buttons(question, markup), loop)
            future.result(timeout=15)

        jalen._offer_buttons = offer_buttons

        def run_turn() -> None:
            # Same dispatch-to-thread + COM-init pattern as run.py --text and
            # app.py's mic loop: process() can block on a RED confirmation or
            # AMBER stop-window, and answering one means a LATER call to
            # process() (the next Telegram message) — which can't happen if
            # this one is running inline on the handler's own thread/loop.
            with jalen_tools.com_initialized():
                jalen.process(text)

        threading.Thread(target=run_turn, daemon=True).start()

    @dp.callback_query()
    async def handle_tap(query) -> None:  # noqa: ANN001
        # The same boundary as a message, applied to the person who TAPPED: in
        # a group, or on a forwarded keyboard, anyone can press a button, and
        # only an allowed id may answer. Silent for everyone else.
        user_id = query.from_user.id if query.from_user else None
        if not is_authorized(user_id, allowed_user_ids):
            return

        tap = jalen.answer_from_tap(query.data or "")
        with contextlib.suppress(Exception):
            await query.answer(tap.toast)       # the little popup on his phone
        if tap.accepted and getattr(query, "message", None) is not None:
            # Take the buttons away so the question looks answered. Best
            # effort: a button left behind is refused by id anyway.
            with contextlib.suppress(Exception):
                await query.message.edit_reply_markup(reply_markup=None)

    return dp


async def run_bot(jalen: Any, bot_token: str, allowed_user_ids: list[int]) -> None:
    """
    Start the bot and poll until cancelled or asked to stop.

    The stop-watcher matters: `python run.py --stop` writes a sentinel and
    waits for the process to exit on its own before resorting to a hard
    kill. Only the voice loop polled that sentinel, so stopping Telegram
    mode always fell through to force-termination — which works, but skips
    the orderly release of the audit DB and the bot session. Racing polling
    against the watcher lets it shut down properly.
    """
    from aiogram import Bot

    from .. import runtime

    bot = Bot(token=bot_token)
    dp = build_dispatcher(jalen, allowed_user_ids)

    async def watch_for_stop() -> None:
        while not runtime.stop_requested() and not jalen._quit.is_set():
            await asyncio.sleep(0.25)

    polling = asyncio.create_task(dp.start_polling(bot))
    watcher = asyncio.create_task(watch_for_stop())
    try:
        done, pending = await asyncio.wait(
            {polling, watcher}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        # Surface a real polling failure rather than exiting silently.
        if polling in done:
            polling.result()
    except asyncio.CancelledError:
        pass
    finally:
        with contextlib.suppress(Exception):
            await dp.stop_polling()
        await bot.session.close()
