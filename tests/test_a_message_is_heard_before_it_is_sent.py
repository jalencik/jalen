"""
A message he is asked to approve is one he has HEARD.

The question before a Telegram text went out as him was "send telegram
message: Ali. Confirm?" - a person, and nothing about what would be said in
his name. Where he had dictated it that is fine; where the brain worded it for
him ("tell Ali I'll be late, nicely") he approved it blind. A voice message
has said its words since it was built (SafetyEngine._voice_message_summary);
now a Telegram or WhatsApp text says them too, as they will be read, and an
email says its subject.

THE ECHO, which is why this needed more than a summary. The question comes
back through the microphone, and a message read out in it can bring back a
word that answers it - "sure, see you at six". The echo test
(Jalen._echoes_the_confirmation) already ignored any echo made only of the
question's own words. Two gaps let a piece of one through as his yes:

  * speech recognition writes the speaker's "ok" back as "okay", and "okay"
    was "a plain answer word the question does not contain", so it settled
    as his answer - one spelling of an answer is now one word to the test;
  * a short mangled fragment of the message ("sure see ya") was under the
    four-word floor. When half or more of it is the MESSAGE's own words and
    it holds one of the message's answer words, it is the echo.

Both fail safe: his own answer made of the message's words in the few seconds
after the question is ignored, and the question times out and cancels.
"""
from __future__ import annotations

import time

import pytest

from jalen.app import ECHO_TAIL_S, Jalen, _quoted_words
from jalen.config import CONFIG
from jalen.safety import SafetyEngine, Tier


def engine():
    e = SafetyEngine(CONFIG)
    e.paranoid = False
    e.posture = "irreversible_only"
    return e


def question(tool, **args):
    return engine().classify(tool, args).summary


# --------------------------------------------------------------------------
# 1. THE QUESTION SAYS THE WORDS
# --------------------------------------------------------------------------
def test_a_telegram_text_to_a_person_is_asked_with_its_words():
    verdict = engine().classify("send_telegram_message",
                                {"to": "Ali", "text": "I&apos;ll be <b>15 minutes</b> late, sorry!"})
    assert verdict.tier is Tier.RED
    assert verdict.summary == "send Ali a Telegram message saying \"I'll be 15 minutes late, sorry!\""


def test_a_link_is_said_as_a_link_and_its_host_named():
    said = question("send_telegram_message", to="Ali",
                    text="the form: https://forms.gle/abc123")
    assert said == 'send Ali a Telegram message saying "the form: a link", with links to forms.gle'


def test_a_reply_and_a_topic_are_said():
    said = question("send_telegram_message", to="ML study group", text="ok",
                    reply_to="#4821", topic="Papers")
    assert said == 'send ML study group a Telegram message as a reply in the topic Papers saying "ok"'


def test_a_long_message_is_clipped_at_a_word():
    said = question("send_telegram_message", to="Ali", text="word " * 200)
    assert said.endswith(' ... and more"') and len(said) < 400


def test_whatsapp_says_its_words_and_email_its_subject():
    assert question("send_whatsapp_message", to="Mom", text="home by 8") == (
        'send Mom a WhatsApp message saying "home by 8"')
    assert question("send_email", to="prof@uni.edu", subject="Application",
                    body="Dear Professor") == 'send an email to prof@uni.edu, subject "Application"'


def test_his_channel_is_still_announced_as_a_post():
    said = question("send_telegram_message", to="AI engineering & Machine learning",
                    text="<b>Meetup</b> moved")
    assert said.startswith("post to AI engineering & Machine learning. It starts")


# --------------------------------------------------------------------------
# 2. THE QUESTION'S OWN ECHO CANNOT ANSWER IT
# --------------------------------------------------------------------------
def _asked(said: str, waited_s: float = 1.0) -> Jalen:
    j = Jalen.__new__(Jalen)
    j._confirm_question = f"{said}. Confirm?"
    j._confirm_asked_at = time.monotonic() - waited_s
    return j


SURE = question("send_telegram_message", to="Ali", text="sure, see you at six")
OK = question("send_telegram_message", to="Ali", text="ok sounds good")


@pytest.mark.parametrize("asked, heard", [
    (OK, "okay sounds good"),          # the speaker's "ok" written back as "okay"
    (OK, "okay"),
    (SURE, "sure see ya"),             # a mangled fragment under the four-word floor
    (SURE, "sure ya"),
    (SURE, "sure see you"),
])
@pytest.mark.parametrize("waited", [0.5, ECHO_TAIL_S - 0.5])
def test_an_echo_of_the_message_is_not_his_answer(asked, heard, waited):
    assert _asked(asked, waited)._echoes_the_confirmation(heard), f"{heard!r} would approve"


@pytest.mark.parametrize("asked, heard", [
    (SURE, "yes"),
    (SURE, "yes send it"),
    (SURE, "no"),
    (OK, "yeah go ahead"),
    (OK, "ok go ahead and send it"),   # one word of the message in five
    (SURE, "sure send it"),            # one word of the message in three
])
def test_his_real_answer_still_counts(asked, heard):
    assert not _asked(asked, 1.0)._echoes_the_confirmation(heard), f"{heard!r} was dropped"


def test_the_words_are_read_from_each_kind_of_question():
    assert _quoted_words(f"{SURE}. Confirm?") == {"sure", "see", "you", "at", "six"}
    voice = ("send Ali a voice message in Jalen's synthetic voice, saying: okay see "
             "you. Confirm?")
    assert {"ok", "see", "you"} <= _quoted_words(voice)
    post = 'post to X. It starts "No meeting today", no links. Confirm?'
    assert _quoted_words(post) == {"no", "meeting", "today"}
    assert _quoted_words("close app: notepad. Confirm?") == set()
