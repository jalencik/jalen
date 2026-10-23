"""
"...or a tap in Telegram" - promised by config/safety.yaml since the first draft.

A question that reached his phone could only be answered by TYPING, on the one
device where that is slowest, inside a window that was tuned for speech. Now a
"Confirm?" arrives with Yes and No under it and an announcement with Stop, and
a tap is the answer.

WHAT MAKES A TAP SAFE TO ACCEPT
-------------------------------
  * only the authorized user's tap is looked at (the same allow-list as a
    message - in a group anyone can press a button);
  * it must carry the id of the question that is open RIGHT NOW, so an old
    keyboard left in the chat cannot answer a new question;
  * it must be the kind of answer that question takes: Stop cannot approve a
    RED action and Yes cannot be read as "stop";
  * it is spent when accepted, so a second tap, or Yes and then No, answers once;
  * it never goes through process(): it is not an instruction, clears no taint,
    and there is no microphone, so no echo to defend against.

The words are always sent, so a door that cannot show buttons, or a keyboard
that fails to send, costs the buttons and nothing else.
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
from types import SimpleNamespace

import pytest

from jalen.app import ConfirmAnswer, Jalen, Tap
from jalen.config import Cfg
from jalen.integrations.telegram_bot import build_dispatcher, prompt_keyboard

ID = "abc12345"


def a_jalen(mode: str = "telegram", cfg: Cfg | None = None) -> Jalen:
    """A Jalen that can run confirm() and announce(), with the real answer queue."""
    j = Jalen.__new__(Jalen)
    j.cfg = cfg or Cfg({"safety": {"confirm_timeout_s": 5, "undo_window_s": 3}})
    j.mode = mode
    j.said = []
    j.say_blocking = lambda text: j.said.append(text)
    j.orb = SimpleNamespace(set_state=lambda *a: None)
    j.errors = []
    j.audit = SimpleNamespace(utterance=lambda *a, **k: None, write=lambda *a, **k: None,
                              error=lambda where, exc: j.errors.append((where, exc)))
    j._answer_q = queue.Queue()
    j._confirm_lock = None
    j._awaiting_confirmation = False
    j._awaiting_stop = False
    j._confirm_question = ""
    j._confirm_asked_at = 0.0
    j._confirm_reasked = False
    j._prompt_id = ""
    j._offer_buttons = None
    return j


def open_confirmation(j: Jalen, prompt_id: str = ID) -> None:
    j._awaiting_confirmation = True
    j._prompt_id = prompt_id


def open_stop_window(j: Jalen, prompt_id: str = ID) -> None:
    j._awaiting_stop = True
    j._prompt_id = prompt_id


# --------------------------------------------------------------------------
# A TAP, ACCEPTED OR NOT
# --------------------------------------------------------------------------
def test_yes_approves_the_open_confirmation():
    j = a_jalen()
    open_confirmation(j)
    tap = j.answer_from_tap(f"jalen:{ID}:yes")
    assert tap == Tap(True, "Approved.")
    answer = j._answer_q.get_nowait()
    assert answer and answer.outcome == "yes"


def test_no_cancels_it_and_is_not_a_timeout():
    j = a_jalen()
    open_confirmation(j)
    tap = j.answer_from_tap(f"jalen:{ID}:no")
    assert tap == Tap(True, "Cancelled.")
    answer = j._answer_q.get_nowait()
    assert not answer and answer.outcome == "no"


def test_stop_stops_an_announcement_the_way_the_kill_phrase_does():
    j = a_jalen()
    open_stop_window(j)
    assert j.answer_from_tap(f"jalen:{ID}:stop") == Tap(True, "Stopped.")
    assert j._answer_q.get_nowait() is False      # what announce() tests with `is False`


def test_a_second_tap_of_the_same_question_answers_nothing():
    j = a_jalen()
    open_confirmation(j)
    assert j.answer_from_tap(f"jalen:{ID}:yes").accepted
    again = j.answer_from_tap(f"jalen:{ID}:yes")
    assert not again.accepted and "already answered" in again.toast
    assert j._answer_q.qsize() == 1


def test_yes_and_then_no_answers_once_not_twice():
    j = a_jalen()
    open_confirmation(j)
    assert j.answer_from_tap(f"jalen:{ID}:yes").accepted
    assert not j.answer_from_tap(f"jalen:{ID}:no").accepted
    assert j._answer_q.qsize() == 1


def test_two_taps_at_the_same_moment_cannot_both_answer():
    j = a_jalen()
    open_confirmation(j)
    results = []
    gate = threading.Barrier(8)

    def tap(choice):
        gate.wait()
        results.append(j.answer_from_tap(f"jalen:{ID}:{choice}").accepted)

    threads = [threading.Thread(target=tap, args=("yes" if i % 2 else "no",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1 and j._answer_q.qsize() == 1


def test_a_button_from_an_older_question_cannot_answer_this_one():
    j = a_jalen()
    open_confirmation(j, prompt_id="newer999")
    tap = j.answer_from_tap(f"jalen:{ID}:yes")
    assert not tap.accepted and j._answer_q.empty()


def test_a_tap_with_no_question_open_answers_nothing():
    j = a_jalen()
    assert not j.answer_from_tap(f"jalen:{ID}:yes").accepted and j._answer_q.empty()


def test_a_tap_after_the_question_ran_out_of_time_answers_nothing():
    j = a_jalen()
    open_confirmation(j)
    j._awaiting_confirmation = False              # confirm() has returned
    tap = j.answer_from_tap(f"jalen:{ID}:yes")
    assert not tap.accepted and "ran out of time" in tap.toast and j._answer_q.empty()


def test_stop_cannot_approve_a_confirmation():
    """Stop is the answer to an announcement; it must not be read as a yes OR a no."""
    j = a_jalen()
    open_confirmation(j)
    assert not j.answer_from_tap(f"jalen:{ID}:stop").accepted
    assert j._answer_q.empty() and j._awaiting_confirmation


def test_yes_cannot_be_taken_for_a_stop():
    j = a_jalen()
    open_stop_window(j)
    assert not j.answer_from_tap(f"jalen:{ID}:yes").accepted
    assert j._answer_q.empty()


def test_a_rejected_tap_leaves_the_question_open_for_the_right_answer():
    j = a_jalen()
    open_confirmation(j)
    j.answer_from_tap(f"jalen:{ID}:stop")
    j.answer_from_tap("jalen:wrongid0:yes")
    assert j.answer_from_tap(f"jalen:{ID}:yes").accepted


@pytest.mark.parametrize("data", [
    "", "yes", "jalen", "jalen:yes", "jalen::yes", f"other:{ID}:yes",
    f"jalen:{ID}:yes:extra", f"jalen:{ID}:maybe", f"jalen:{ID}:", None,
])
def test_anything_that_is_not_one_of_my_buttons_is_refused(data):
    j = a_jalen()
    open_confirmation(j)
    open_stop_window(j)
    assert not j.answer_from_tap(data).accepted
    assert j._answer_q.empty()


def test_the_id_is_empty_when_nothing_is_asked_and_an_empty_id_never_matches():
    j = a_jalen()
    j._awaiting_confirmation = True
    j._prompt_id = ""
    assert not j.answer_from_tap("jalen::yes").accepted
    assert not j.answer_from_tap("jalen: :yes").accepted


# --------------------------------------------------------------------------
# PUTTING THE QUESTION OUT
# --------------------------------------------------------------------------
def test_without_buttons_the_question_is_only_said():
    j = a_jalen()
    j._put_the_question("delete file: x. Confirm?", "confirm")
    assert j.said == ["delete file: x. Confirm?"] and len(j._prompt_id) == 8


def test_with_buttons_the_question_goes_out_with_them_and_is_not_also_said():
    j = a_jalen()
    sent = []
    j._offer_buttons = lambda question, kind, pid: sent.append((question, kind, pid))
    j._put_the_question("delete file: x. Confirm?", "confirm")
    assert sent == [("delete file: x. Confirm?", "confirm", j._prompt_id)]
    assert j.said == []


def test_every_question_gets_its_own_id():
    j = a_jalen()
    ids = set()
    for _ in range(50):
        j._put_the_question("q", "confirm")
        ids.add(j._prompt_id)
    assert len(ids) == 50


def test_buttons_that_fail_to_send_fall_back_to_the_words_and_say_so():
    j = a_jalen()

    def broken(question, kind, pid):
        raise RuntimeError("Telegram said no")

    j._offer_buttons = broken
    j._put_the_question("delete file: x. Confirm?", "confirm")
    assert j.said == ["delete file: x. Confirm?"]
    assert j.errors and j.errors[0][0] == "prompt.buttons"


def test_confirm_sends_its_question_with_buttons_and_a_tap_answers_it():
    j = a_jalen()
    sent = []
    j._offer_buttons = lambda question, kind, pid: sent.append((question, kind, pid))
    result = {}

    def ask():
        result["answer"] = asyncio.run(j.confirm("delete file: x. Confirm?"))

    worker = threading.Thread(target=ask)
    worker.start()
    deadline = time.monotonic() + 3
    while not j._awaiting_confirmation and time.monotonic() < deadline:
        time.sleep(0.01)
    assert j._awaiting_confirmation and sent[0][1] == "confirm"
    assert j.answer_from_tap(f"jalen:{sent[0][2]}:yes").accepted
    worker.join(3)
    assert result["answer"] and result["answer"].outcome == "yes"
    assert j._prompt_id == "", "the spent question's id was left behind"


def test_a_tap_of_no_comes_back_to_the_caller_as_a_no_and_not_a_timeout():
    j = a_jalen()
    sent = []
    j._offer_buttons = lambda question, kind, pid: sent.append(pid)
    result = {}
    worker = threading.Thread(
        target=lambda: result.update(answer=asyncio.run(j.confirm("send email. Confirm?"))))
    worker.start()
    deadline = time.monotonic() + 3
    while not j._awaiting_confirmation and time.monotonic() < deadline:
        time.sleep(0.01)
    j.answer_from_tap(f"jalen:{sent[0]}:no")
    worker.join(3)
    assert not result["answer"] and result["answer"].outcome == "no"


def test_announce_sends_a_stop_button_and_a_tap_cancels_the_action():
    j = a_jalen()
    sent = []
    j._offer_buttons = lambda question, kind, pid: sent.append((kind, pid))
    result = {}

    def announce():
        try:
            asyncio.run(j.announce("edit file x. Say stop if you don't want that."))
            result["outcome"] = "went ahead"
        except RuntimeError as exc:
            result["outcome"] = str(exc)

    worker = threading.Thread(target=announce)
    worker.start()
    deadline = time.monotonic() + 3
    while not j._awaiting_stop and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sent[0][0] == "stop"
    assert j.answer_from_tap(f"jalen:{sent[0][1]}:stop").accepted
    worker.join(5)
    assert result["outcome"] == "cancelled by user"


def test_an_announcement_nobody_stops_goes_ahead_and_spends_its_button():
    j = a_jalen(cfg=Cfg({"safety": {"undo_window_s": 0.05, "undo_window_typed_s": 0.05}}))
    sent = []
    j._offer_buttons = lambda question, kind, pid: sent.append(pid)
    asyncio.run(j.announce("edit file x. Say stop if you don't want that."))
    assert j._prompt_id == ""
    assert not j.answer_from_tap(f"jalen:{sent[0]}:stop").accepted


# --------------------------------------------------------------------------
# THE STOP WINDOW, WHERE "STOP" HAS TO BE TYPED OR TAPPED
# --------------------------------------------------------------------------
def test_by_voice_the_stop_window_is_still_the_short_one():
    assert a_jalen("voice", Cfg({"safety": {"undo_window_s": 2}}))._undo_window_s() == 2.0


@pytest.mark.parametrize("mode", ["text", "telegram"])
def test_where_stop_is_typed_or_tapped_the_window_is_long_enough_to_use(mode):
    j = a_jalen(mode, Cfg({"safety": {"undo_window_s": 2, "undo_window_typed_s": 10}}))
    assert j._undo_window_s() == 10.0


def test_the_typed_stop_window_is_never_shorter_than_the_spoken_one():
    assert a_jalen("telegram", Cfg({"safety": {"undo_window_s": 6, "undo_window_typed_s": 1}}))._undo_window_s() == 6.0


def test_the_shipped_typed_stop_window_says_it_is_a_guess():
    from jalen.config import CONFIG

    assert CONFIG.get_path("safety.undo_window_typed_s") == 10
    raw = open("config/jalen.yaml", encoding="utf-8").read()
    comment, _, _ = raw.partition("undo_window_typed_s: 10")
    assert "NOT MEASURED" in comment[-700:]


# --------------------------------------------------------------------------
# THE KEYBOARD
# --------------------------------------------------------------------------
def buttons(markup):
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


def test_a_confirmation_gets_yes_and_no():
    assert buttons(prompt_keyboard("confirm", ID)) == [("Yes", f"jalen:{ID}:yes"),
                                                      ("No", f"jalen:{ID}:no")]


def test_an_announcement_gets_go_ahead_and_stop():
    assert buttons(prompt_keyboard("stop", ID)) == [("Go ahead", f"jalen:{ID}:go"),
                                                   ("Stop", f"jalen:{ID}:stop")]


def test_go_ahead_ends_the_stop_window_early_without_stopping_anything():
    j = a_jalen()
    open_stop_window(j)
    assert j.answer_from_tap(f"jalen:{ID}:go") == Tap(True, "Going ahead.")
    assert j._answer_q.get_nowait() is True


def test_go_ahead_can_never_answer_a_confirmation():
    """It ends a wait for "stop"; it is not a yes to a RED action."""
    j = a_jalen()
    open_confirmation(j)
    assert not j.answer_from_tap(f"jalen:{ID}:go").accepted
    assert j._answer_q.empty()


def test_announce_lets_go_ahead_through_at_once():
    j = a_jalen(cfg=Cfg({"safety": {"undo_window_s": 30, "undo_window_typed_s": 30}}))
    sent = []
    j._offer_buttons = lambda question, kind, pid: sent.append(pid)
    result = {}

    def announce():
        started = time.monotonic()
        asyncio.run(j.announce("edit file x. Say stop if you don't want that."))
        result["took"] = time.monotonic() - started

    worker = threading.Thread(target=announce)
    worker.start()
    deadline = time.monotonic() + 3
    while not j._awaiting_stop and time.monotonic() < deadline:
        time.sleep(0.01)
    assert j.answer_from_tap(f"jalen:{sent[0]}:go").accepted
    worker.join(5)
    assert result["took"] < 5, "went ahead only when the 30 s window ran out"


def test_an_unknown_kind_has_no_buttons_rather_than_the_wrong_ones():
    with pytest.raises(ValueError):
        prompt_keyboard("approve-everything", ID)


@pytest.mark.parametrize("kind", ["confirm", "stop"])
def test_callback_data_fits_telegrams_limit_with_room_to_spare(kind):
    for _text, data in buttons(prompt_keyboard(kind, "f" * 8)):
        assert len(data.encode("utf-8")) <= 24 < 64


def test_every_button_the_keyboard_can_carry_is_one_answer_from_tap_understands():
    j = a_jalen()
    for kind, opener in (("confirm", open_confirmation), ("stop", open_stop_window)):
        for _text, data in buttons(prompt_keyboard(kind, ID)):
            j = a_jalen()
            opener(j)
            assert j.answer_from_tap(data).accepted, data


# --------------------------------------------------------------------------
# THE DISPATCHER
# --------------------------------------------------------------------------
class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeKeyboardMessage:
    def __init__(self):
        self.edits = []

    async def edit_reply_markup(self, reply_markup=None):
        self.edits.append(reply_markup)


class FakeQuery:
    def __init__(self, user_id, data):
        self.from_user = FakeUser(user_id) if user_id is not None else None
        self.data = data
        self.answered = []
        self.message = FakeKeyboardMessage()

    async def answer(self, text=None, **_):
        self.answered.append(text)


class FakeJalen:
    def __init__(self, accepted=True):
        self.say = self.say_blocking = None
        self.processed = []
        self.taps = []
        self.accepted = accepted

    def process(self, text):
        self.processed.append(text)

    def answer_from_tap(self, data):
        self.taps.append(data)
        return Tap(self.accepted, "Approved." if self.accepted else "Too late.")


def tap_handler(jalen, allowed):
    dp = build_dispatcher(jalen, allowed)
    return dp.callback_query.handlers[0].callback


def test_a_tap_from_a_stranger_reaches_nothing_and_gets_no_answer():
    jalen = FakeJalen()
    query = FakeQuery(user_id=999, data=f"jalen:{ID}:yes")
    asyncio.run(tap_handler(jalen, [123])(query))
    assert jalen.taps == [] and query.answered == [] and query.message.edits == []


def test_a_tap_with_no_sender_is_never_authorized():
    jalen = FakeJalen()
    asyncio.run(tap_handler(jalen, [123])(FakeQuery(user_id=None, data=f"jalen:{ID}:yes")))
    assert jalen.taps == []


def test_an_empty_allow_list_refuses_every_tap():
    jalen = FakeJalen()
    asyncio.run(tap_handler(jalen, [])(FakeQuery(user_id=123, data=f"jalen:{ID}:yes")))
    assert jalen.taps == []


def test_his_tap_is_passed_on_answered_and_the_buttons_come_off():
    jalen = FakeJalen()
    query = FakeQuery(user_id=123, data=f"jalen:{ID}:yes")
    asyncio.run(tap_handler(jalen, [123])(query))
    assert jalen.taps == [f"jalen:{ID}:yes"]
    assert query.answered == ["Approved."]
    assert query.message.edits == [None]


def test_a_tap_that_was_too_late_says_so_and_leaves_the_message_alone():
    jalen = FakeJalen(accepted=False)
    query = FakeQuery(user_id=123, data=f"jalen:{ID}:yes")
    asyncio.run(tap_handler(jalen, [123])(query))
    assert query.answered == ["Too late."] and query.message.edits == []


def test_a_tap_is_never_an_instruction():
    jalen = FakeJalen()
    asyncio.run(tap_handler(jalen, [123])(FakeQuery(user_id=123, data=f"jalen:{ID}:yes")))
    assert jalen.processed == []


def test_a_failure_to_tidy_up_does_not_undo_the_answer():
    class Hostile(FakeQuery):
        async def answer(self, text=None, **_):
            raise RuntimeError("query too old")

    jalen = FakeJalen()
    asyncio.run(tap_handler(jalen, [123])(Hostile(user_id=123, data=f"jalen:{ID}:yes")))
    assert jalen.taps == [f"jalen:{ID}:yes"]


def test_the_bot_listens_for_taps_at_all():
    """aiogram only asks Telegram for the update types something handles."""
    dp = build_dispatcher(FakeJalen(), [123])
    assert "callback_query" in dp.resolve_used_update_types()


class FakeAnswer:
    """message.answer(): not async, returns something awaitable that is not a coroutine."""

    def __init__(self, calls):
        self.calls = calls

    def __call__(self, text, reply_markup=None):
        self.calls.append((text, reply_markup))
        return self

    def __await__(self):
        return iter(())


class FakeMessage:
    def __init__(self, user_id, text):
        self.from_user = FakeUser(user_id)
        self.text = text
        self.calls = []
        self.answer = FakeAnswer(self.calls)


def test_a_message_installs_a_sender_that_puts_the_buttons_under_the_question():
    jalen = FakeJalen()
    handler = build_dispatcher(jalen, [123]).message.handlers[0].callback
    message = FakeMessage(123, "delete my temp files")

    async def scenario():
        await handler(message)
        assert jalen._offer_buttons is not None
        # Called from a worker thread, as in production: it blocks on the loop.
        await asyncio.get_running_loop().run_in_executor(
            None, jalen._offer_buttons, "clear temp files. Confirm?", "confirm", ID)

    asyncio.run(scenario())
    (text, markup), = message.calls
    assert text == "clear temp files. Confirm?"
    assert buttons(markup) == [("Yes", f"jalen:{ID}:yes"), ("No", f"jalen:{ID}:no")]


def test_a_sender_for_a_kind_it_cannot_make_raises_so_the_words_are_sent_instead():
    jalen = FakeJalen()
    handler = build_dispatcher(jalen, [123]).message.handlers[0].callback
    message = FakeMessage(123, "hello")

    async def scenario():
        await handler(message)
        with pytest.raises(ValueError):
            await asyncio.get_running_loop().run_in_executor(
                None, jalen._offer_buttons, "q", "nonsense", ID)

    asyncio.run(scenario())
    assert message.calls == []


def test_an_unauthorized_message_installs_no_sender():
    jalen = FakeJalen()
    handler = build_dispatcher(jalen, [123]).message.handlers[0].callback
    asyncio.run(handler(FakeMessage(999, "hello")))
    assert not hasattr(jalen, "_offer_buttons")
