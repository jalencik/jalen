"""
scripts/live_telegram_check.py - the owner-run check of premium emoji,
stickers and voice on his REAL Telegram - driven end to end against fakes.

Nothing built for premium emoji, stickers or voice messages has run against
his account (stickers.py: "NOT VERIFIED ON HIS ACCOUNT"), so the script is the
first thing that will, and he runs it himself. What it must never do is what
these tests pin:

  * send anywhere but Saved Messages, or send without a typed yes;
  * run while Jalen holds the Telegram session, let Jalen start mid-run, or run
    from a work copy whose lock Jalen never reads;
  * print a message, an emoji id, his name or his handle - the tool replies it
    reads quote the post and name the account;
  * report a check as passed when it did not happen (a failed read-back, a
    lookup that returned another emoji, a post that could not be tried);
  * lose the table, the lock or the connection on Ctrl+C, a stop, or a
    Telegram that cannot be reached.

The real tool functions run (send_telegram_message, send_voice_message,
find_premium_emoji, list_sticker_packs, telegram_status) against
tests/_telegram_fakes.FakeAccount. Only the transport (RUNTIME.run), the lock's
folder and speech rendering are replaced. No network, no session, no data/.
"""
from __future__ import annotations

import asyncio
import os
import types

import psutil
import pytest

from jarvis import runtime
from jarvis.integrations import telegram_user
from jarvis.tools import messaging, stickers
from scripts import live_telegram_check as live
from tests._telegram_fakes import FakeAccount, FakeChannel, emoji_doc, set_info, sticker_doc

ROCKET, CLAP, PIN = "\U0001f680", "\U0001f44f", "\U0001f4cc"
IDS = (5368324170671202286, 5368324170671202287, 5368324170671202288)


class _Account(FakeAccount):
    """FakeAccount that records where each send went, and marks voice notes."""

    def __init__(self, *, keeps_premium=True, marks_voice=True, premium_flag=None):
        super().__init__(keeps_premium=keeps_premium)
        self.marks_voice = marks_voice
        self.premium_flag = premium_flag      # what get_me says, when it differs
        self.sent_to: list = []

    async def get_me(self, input_peer=False):
        me = await super().get_me(input_peer)
        if self.premium_flag is not None:
            me.premium = self.premium_flag
        return me

    async def send_message(self, entity, message="", **kwargs):
        self.sent_to.append(entity)
        return await super().send_message(entity, message, **kwargs)

    async def send_file(self, entity, file, **kwargs):
        self.sent_to.append(entity)
        sent = await super().send_file(entity, file, **kwargs)
        sent.voice = True if (self.marks_voice and kwargs.get("voice_note")) else None
        return sent


def _account(*, pack_title="Tech Memes", with_sets=True, no_pin=False, packs=1, **kwargs):
    acct = _Account(**kwargs)
    if with_sets:
        docs = [emoji_doc(IDS[0], ROCKET, 100), emoji_doc(IDS[1], CLAP, 100)]
        words = {IDS[0]: ["rocket", "launch"], IDS[1]: ["clap"]}
        if no_pin:
            words[IDS[0]].append("spinning")       # "pin" is inside it
        else:
            docs.append(emoji_doc(IDS[2], PIN, 100))
            words[IDS[2]] = ["pin"]
        acct.install_emoji_set(set_info(100, "Launch Pack", "launch_pack", len(docs), emojis=True),
                               docs, keywords=words)
        for n in range(packs):
            acct.install_sticker_set(
                set_info(200 + n, pack_title if n == 0 else f"Pack {n}", f"pack_{n}", 2),
                [sticker_doc(2001 + 10 * n, ROCKET, 200 + n), sticker_doc(2002 + 10 * n, CLAP, 200 + n)])
    return acct


@pytest.fixture
def telegram(monkeypatch, tmp_path):
    """His account behind the real tools; the lock in tmp_path; sends recorded."""
    state = types.SimpleNamespace(account=_account(), runs=0, shutdowns=0, sends=[])

    def run(work, timeout=60.0):
        state.runs += 1
        return asyncio.run(work(state.account))

    def shutdown():
        state.shutdowns += 1

    monkeypatch.setattr(messaging.RUNTIME, "run", run)
    monkeypatch.setattr(messaging.RUNTIME, "shutdown", shutdown)

    for name in ("send_telegram_message", "send_voice_message"):
        real = getattr(messaging, name)

        def recording(*, to, text, _real=real, _name=name):
            state.sends.append((_name, to))
            return _real(to=to, text=text)

        monkeypatch.setattr(messaging, name, recording)

    real_get = messaging.CONFIG.get_path
    monkeypatch.setattr(messaging.CONFIG, "get_path", lambda key, default=None: (
        True if key == "telegram.personal.enabled" else real_get(key, default)))
    monkeypatch.setattr(messaging, "_enabled", lambda: None)
    monkeypatch.setattr(messaging, "have_session", lambda: True)
    monkeypatch.setattr(telegram_user, "have_session", lambda: True)
    monkeypatch.setattr(messaging, "_speech_mp3", lambda words, language="": b"mp3")
    monkeypatch.setattr(messaging, "_to_voice_note", lambda mp3: (b"OggS" + bytes(16), 3.4))
    monkeypatch.setattr(messaging, "_voice_notice_given", False)

    for name, leaf in (("RUNTIME_DIR", ""), ("LOCK_PATH", "jarvis.lock"),
                       ("STOP_PATH", "jarvis.stop"), ("SIGNAL_PATH", "jalen.signal")):
        monkeypatch.setattr(runtime, name, tmp_path / leaf if leaf else tmp_path)
    # The suite itself may run from a work copy; the check refuses those.
    monkeypatch.setattr(live, "ROOT", tmp_path / "jarvis")

    stickers._forget()
    yield state
    stickers._forget()


def _answers(*replies):
    """An `ask` that gives these answers in order; an exception class is raised."""
    queue = list(replies)

    def ask(question):
        ask.asked.append(question)
        reply = queue.pop(0)
        if isinstance(reply, type) and issubclass(reply, BaseException):
            raise reply
        return reply() if callable(reply) else reply

    ask.asked = []
    return ask


def _never(question):
    raise AssertionError(f"asked {question!r} when nothing should be asked")


def _paste_block(out: str) -> str:
    return out[out.index("--- paste this back"):]


# ------------------------------------------------------------ where it sends
def test_a_full_run_sends_two_things_to_saved_messages_and_nowhere_else(telegram, capsys):
    assert live.main([], ask=_answers(True, True)) == 0
    assert telegram.sends == [("send_telegram_message", "Saved Messages"),
                              ("send_voice_message", "Saved Messages")]
    assert len(telegram.account.sent_to) == 2
    assert all(getattr(entity, "is_self", False) for entity in telegram.account.sent_to)
    out = capsys.readouterr().out
    assert "KEPT" in out and "arrived as a voice message" in out


def test_it_asks_before_each_send_and_no_sends_nothing(telegram, capsys):
    ask = _answers(False, False)
    assert live.main([], ask=ask) == 0
    assert len(ask.asked) == 2 and all("Saved Messages" in q for q in ask.asked)
    assert telegram.account.sent_to == [] and telegram.sends == []
    assert capsys.readouterr().out.count("you said no") >= 2


def test_only_y_or_yes_counts_as_yes(monkeypatch):
    for typed, meant in (("y", True), ("YES ", True), ("", False), ("yeah", False),
                         ("n", False), ("sure", False)):
        monkeypatch.setattr("builtins.input", lambda prompt, _t=typed: _t)
        assert live._ask("send?") is meant, typed


def test_no_send_mode_neither_asks_nor_sends(telegram):
    assert live.main(["--no-send"], ask=_never) == 0
    assert telegram.account.sent_to == [] and telegram.sends == []


def test_there_is_no_way_to_name_another_destination():
    for option in (["--to", "Ali"], ["--chat", "Ali"], ["--destination", "Ali"], ["Ali"]):
        with pytest.raises(SystemExit):
            live._parse(option)


def test_any_other_name_is_refused_before_telegram_is_asked(telegram):
    # main() only ever passes the constant; this is the guard for any later
    # caller of _send, and it must not even look the name up.
    for name in ("Ali", "AI engineering & Machine learning", "me", "saved messages"):
        with pytest.raises(live._WrongChat):
            live._only_saved_messages(name)
    assert telegram.runs == 0


def test_a_name_that_is_not_his_own_account_is_never_sent_to(telegram, monkeypatch, capsys):
    # If "Saved Messages" ever stopped meaning get_me() inside _resolve, it
    # would be searched among his chats - and a group can be called that.
    async def a_group_called_saved_messages(client, name):
        return FakeChannel("Saved Messages", id=77)

    monkeypatch.setattr(messaging, "_resolve", a_group_called_saved_messages)
    assert live.main([], ask=_answers(True, True)) == 1
    assert telegram.account.sent_to == [] and telegram.sends == []
    assert "did not resolve to your own account" in capsys.readouterr().out


def test_a_reply_naming_another_chat_stops_all_sending(telegram, monkeypatch, capsys):
    monkeypatch.setattr(messaging, "send_telegram_message",
                        lambda *, to, text: "Sent to Ali: 'something'")
    assert live.main([], ask=_answers(True, True)) == 1
    out = capsys.readouterr().out
    assert "WRONG CHAT" in out and "Nothing more was sent" in out
    assert not any(name == "send_voice_message" for name, _ in telegram.sends)


# ------------------------------------------------------- one Telegram client
def _hold_lock(mode: str) -> None:
    me = psutil.Process(os.getpid())
    runtime.LOCK_PATH.write_text(f"{me.pid}\n{me.create_time()}\n{mode}\n", encoding="utf-8")


def test_it_will_not_start_while_jalen_holds_the_session(telegram, capsys):
    _hold_lock("voice")
    assert live.main([], ask=_never) == 2
    assert telegram.runs == 0
    assert runtime.LOCK_PATH.read_text(encoding="utf-8").endswith("voice\n"), "his lock was touched"
    assert "run.py --stop" in capsys.readouterr().out


def test_a_second_check_is_told_a_check_is_running_not_to_stop_jalen(telegram, capsys):
    _hold_lock(live.LOCK_MODE)
    assert live.main([], ask=_never) == 2
    out = capsys.readouterr().out
    # The intro warns against run.py --stop; the advice must not be to run it.
    assert "Another live Telegram check" in out and "Stop it first" not in out


def test_jalen_cannot_start_while_the_check_holds_the_session(telegram, monkeypatch):
    seen = []
    inner = messaging.RUNTIME.run

    def run(work, timeout=60.0):
        seen.append(runtime.acquire("voice"))      # what run.py does on start
        return inner(work, timeout)

    monkeypatch.setattr(messaging.RUNTIME, "run", run)
    assert live.main([], ask=_answers(True, True)) == 0
    assert seen and all(info is not None and info.mode == live.LOCK_MODE for info in seen)
    assert runtime.running_instance() is None, "the lock outlived the check"


def test_a_work_copy_refuses_to_run(telegram, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(live, "ROOT", tmp_path / "jarvis" / ".claude" / "worktrees" / "collab-x")
    assert live.main([], ask=_never) == 2
    assert telegram.runs == 0 and not runtime.LOCK_PATH.exists()
    assert "main Jalen folder" in capsys.readouterr().out


def test_not_signed_in_stops_before_telegram_and_says_how(telegram, monkeypatch, capsys):
    monkeypatch.setattr(telegram_user, "have_session", lambda: False)
    assert live.main([], ask=_never) == 2
    assert telegram.runs == 0
    assert "connect_telegram.py" in capsys.readouterr().out
    assert not runtime.LOCK_PATH.exists()


# -------------------------------------------------------- what it prints
@pytest.mark.parametrize("account", [
    lambda: _account(),
    lambda: _account(keeps_premium=False),               # DROPPED path
    lambda: _account(marks_voice=False),                 # WRONG KIND path
], ids=["kept", "dropped", "wrong-kind"])
def test_nothing_printed_names_a_message_an_id_or_him(telegram, capsys, account):
    telegram.account = account()
    live.main([], ask=_answers(True, True))
    out = capsys.readouterr().out
    for doc_id in IDS:
        assert str(doc_id) not in out
    for private in ("tg-emoji", "Iht_student", "Jaloliddin", live.VOICE_WORDS, "safe to delete"):
        assert private not in out, private


def test_an_unexpected_reply_is_printed_without_its_ids(telegram, monkeypatch, capsys):
    # The happy path prints fixed sentences; an odd reply is printed as its
    # reason, and that is where a tag or a number could get out.
    tag = f'<tg-emoji emoji-id="{IDS[0]}">{ROCKET}</tg-emoji>'
    monkeypatch.setattr(messaging, "send_telegram_message", lambda *, to, text: (
        f"Nothing sent — Telegram does not know {tag} (id {IDS[1]}), call +998901234567."))
    live.main([], ask=_answers(True, False))
    out = capsys.readouterr().out
    assert "NOTHING SENT" in out
    for private in (str(IDS[0]), str(IDS[1]), "tg-emoji", "998901234567"):
        assert private not in out, private


def test_pack_titles_reach_his_terminal_without_control_characters(telegram, capsys):
    telegram.account = _account(pack_title="Evil\x1b[2J\x1b]0;owned\x07Pack")
    live.main(["--no-send"], ask=_never)
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out
    assert "Evil" in out and "Pack" in out


def test_a_cut_off_pack_list_says_so(telegram, monkeypatch, capsys):
    monkeypatch.setattr(stickers, "_LIST_LIMIT", 1)
    telegram.account = _account(packs=2)
    live.main(["--no-send"], ask=_never)
    assert "(showing 1 of 2)" in capsys.readouterr().out


def test_the_paste_block_carries_the_measurements_in_plain_ascii(telegram, capsys):
    live.main([], ask=_answers(True, True))
    block = _paste_block(capsys.readouterr().out)
    for line in ("lookup, first (reads every set):", "lookup, cached (average):",
                 "emoji sets: 1", "sticker packs: 1", "found by character: 3 of 3",
                 "found by name: 3 of 3", "account has Telegram Premium: yes",
                 "voice message length: 0:03", "premium rocket (U+1F680) by character: FOUND"):
        assert line in block, line
    block.encode("ascii")    # saved with "> file.txt" on a cp1251 console, nothing turns into "?"


# ------------------------------------------------------------- outcomes
def test_a_post_whose_premium_emoji_was_dropped_is_a_failure(telegram, capsys):
    telegram.account = _account(keeps_premium=False)
    assert live.main([], ask=_answers(True, True)) == 1
    out = capsys.readouterr().out
    assert "DROPPED" in out and "account has Telegram Premium: no" in out


def test_kept_in_saved_messages_without_premium_says_a_channel_post_was_not_tested(telegram, capsys):
    telegram.account = _account(premium_flag=False)      # Saved Messages kept them anyway
    assert live.main([], ask=_answers(True, True)) == 0
    assert "a channel post was not tested" in capsys.readouterr().out


def test_no_premium_emoji_found_means_the_post_was_not_tested(telegram, capsys):
    telegram.account = _account(with_sets=False)
    ask = _answers(True)
    assert live.main([], ask=ask) == 1                   # the main question went unanswered
    assert len(ask.asked) == 1 and "voice" in ask.asked[0]
    assert telegram.sends == [("send_voice_message", "Saved Messages")]
    assert "NOT TESTED" in capsys.readouterr().out


def test_a_name_that_finds_a_different_emoji_is_not_counted_as_found(telegram, capsys):
    telegram.account = _account(no_pin=True)             # "pin" matches "spinning" on the rocket
    live.main(["--no-send"], ask=_never)
    out = capsys.readouterr().out
    assert "OTHER EMOJI" in out
    assert "found by name: 2 of 3" in out and "found by character: 2 of 3" in out


def test_a_voice_message_that_arrives_as_something_else_is_a_failure(telegram, capsys):
    telegram.account = _account(marks_voice=False)
    assert live.main([], ask=_answers(False, True)) == 1
    assert "WRONG KIND" in capsys.readouterr().out


def test_a_voice_read_back_that_failed_is_not_reported_as_arrived(telegram, capsys):
    # Its failure sentence contains "Telegram shows it as a voice message".
    telegram.account.fail_read_back = True
    assert live.main([], ask=_answers(False, True)) == 1
    out = capsys.readouterr().out
    assert "NOT CHECKED" in out and "arrived as a voice message" not in out


# --------------------------------------------------- Telegram unreachable
def test_telegram_unreachable_at_the_start_is_a_sentence_not_a_traceback(telegram, monkeypatch, capsys):
    def offline(work, timeout=60.0):
        raise ConnectionError("Connection to Telegram failed 5 time(s)")

    monkeypatch.setattr(messaging.RUNTIME, "run", offline)
    assert live.main([], ask=_never) == 2
    out = capsys.readouterr().out
    assert "ConnectionError" in out and "check the internet" in out and "STEP" in out
    assert not runtime.LOCK_PATH.exists() and telegram.shutdowns == 1


def test_telegram_lost_mid_run_keeps_the_measurements(telegram, monkeypatch, capsys):
    inner = messaging.RUNTIME.run

    def drops_at_the_send(work, timeout=60.0):
        if getattr(work, "__name__", "") == "is_his_own":
            raise TimeoutError
        return inner(work, timeout)

    monkeypatch.setattr(messaging.RUNTIME, "run", drops_at_the_send)
    assert live.main([], ask=_answers(True, True)) == 1
    out = capsys.readouterr().out
    assert "TimeoutError" in out and "found by character: 3 of 3" in _paste_block(out)
    assert telegram.account.sent_to == []


# ------------------------------------------------------- Ctrl+C and stops
def test_ctrl_c_prints_the_table_closes_telegram_and_releases_the_lock(telegram, capsys):
    assert live.main([], ask=_answers(KeyboardInterrupt)) == 130
    out = capsys.readouterr().out
    assert "STOPPED" in out and "STEP" in out
    assert telegram.shutdowns == 1
    assert not runtime.LOCK_PATH.exists()
    assert telegram.account.sent_to == []


def test_ctrl_c_during_a_send_says_it_may_have_arrived(telegram, monkeypatch, capsys):
    def interrupted(*, to, text):
        raise KeyboardInterrupt

    monkeypatch.setattr(messaging, "send_telegram_message", interrupted)
    assert live.main([], ask=_answers(True, True)) == 130
    assert "may have reached Telegram" in capsys.readouterr().out
    assert not runtime.LOCK_PATH.exists()


def test_a_stop_asked_between_steps_ends_the_run_cleanly(telegram, capsys):
    # Jalen's kill hotkey, run.py --stop and jalen.ps1 restart write the stop
    # file and kill whatever holds the lock 8 s later.
    def yes_and_someone_asks_jalen_to_stop():
        runtime.request_stop()
        return True

    assert live.main([], ask=_answers(yes_and_someone_asks_jalen_to_stop, True)) == 130
    out = capsys.readouterr().out
    assert "Jalen's stop or restart was asked for" in out
    assert telegram.account.sent_to == [] and not runtime.LOCK_PATH.exists()
