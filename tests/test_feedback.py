"""
"How do you rate my work out of ten?" — and when NOT to ask it.

    "after the work has been done, it should ask the user, Hey boss, 'How do
     you rate my work out of 10', once the user gives feedback about the
     process, that feedback should come to my email... in simple words,
     summary format"

THE RESTRAINT IS THE FEATURE
----------------------------
Asking is four lines. Knowing when not to is the whole design. An assistant
that requests a score after "what's the time" is not collecting feedback, it
is collecting resentment — and the numbers it gets back are worthless anyway,
because nobody considers a question they are asked forty times a day.

So worth_asking_about() says NO by default and yes only when all three hold:
a tool with a real effect ran, the turn took real time, and he has not been
asked recently. Most of the tests here are about the noes.
"""
from __future__ import annotations

import json

import pytest

from jalen.tools import feedback


# ---------------------------------------------------------------------------
# When to ask
# ---------------------------------------------------------------------------
NEVER_ASK = [
    (["get_time"], 2.0, "what's the time"),
    (["get_weather"], 4.0, "what's the weather"),
    (["unread_email_headline"], 6.0, "any new email"),
    (["disk_report"], 8.0, "how much space have I got"),
    ([], 40.0, "a long turn that did nothing at all"),
    (["web_delegate"], 4.0, "a delegation that failed immediately"),
    (["jalen_orb_size"], 30.0, "make yourself bigger"),
    (["own_health"], 30.0, "are you ok"),
]


@pytest.mark.parametrize("tools,seconds,label", NEVER_ASK,
                         ids=[c[2] for c in NEVER_ASK])
def test_it_does_not_ask_after_ordinary_turns(tools, seconds, label):
    assert not feedback.worth_asking_about(
        tools, seconds, now=10_000, last_asked=0
    ), f"it would ask for a score after {label!r}"


ASK = [
    (["web_delegate"], 90.0, "delegated to ChatGPT"),
    (["start_coding_job"], 400.0, "ran a coding job"),
    (["send_email"], 30.0, "sent an email"),
    (["scan_inbox", "draft_telegram_post"], 120.0, "sorted the inbox and drafted"),
    (["clear_temp_files"], 45.0, "deleted files"),
]


@pytest.mark.parametrize("tools,seconds,label", ASK, ids=[c[2] for c in ASK])
def test_it_does_ask_after_real_work(tools, seconds, label):
    assert feedback.worth_asking_about(tools, seconds, now=10_000, last_asked=0)


def test_it_will_not_ask_twice_in_the_same_stretch():
    """
    Twenty minutes. Being asked for a score three times in an afternoon is
    how a feedback prompt becomes something you learn to talk over.
    """
    just_now = 10_000 - 60
    assert not feedback.worth_asking_about(
        ["web_delegate"], 200.0, now=10_000, last_asked=just_now
    )
    long_ago = 10_000 - feedback.COOLDOWN_S - 1
    assert feedback.worth_asking_about(
        ["web_delegate"], 200.0, now=10_000, last_asked=long_ago
    )


def test_the_question_is_the_one_he_asked_for():
    question = feedback.the_question()
    assert "boss" in question.lower()
    assert "ten" in question.lower() or "10" in question


# ---------------------------------------------------------------------------
# Hearing the answer
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("said,expected", [
    ("8", 8),
    ("eight", 8),
    ("I'd say about a seven", 7),
    ("8 out of 10", 8),
    ("ten out of ten", 10),
    ("2/10", 2),
    ("nine, good job", 9),
    ("zero", 0),
    ("a solid 6 I think", 6),
])
def test_a_score_is_heard_however_he_says_it(said, expected):
    """
    People answer this in words at least as often as digits. An assistant
    that only accepts "7" has asked a question it cannot hear the answer to.
    """
    assert feedback.parse_rating(said) == expected


@pytest.mark.parametrize("said", [
    "it was fine", "good job", "terrible", "yeah alright", "",
])
def test_a_non_answer_is_not_invented_into_a_score(said):
    """
    None, not a guess. A made-up 7 in his inbox is worse than no mail: it
    reads as data and it is fiction.
    """
    assert feedback.parse_rating(said) is None


# ---------------------------------------------------------------------------
# What leaves the machine
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("dangerous", [
    "unlock the vault, my passphrase is correct horse battery",
    "my password is hunter2",
    "lock the vault again",
])
def test_a_sentence_about_a_credential_is_dropped_entirely(dangerous):
    """
    Not partially redacted — dropped. "my password is ***redacted***" still
    tells a reader exactly what the next word was.
    """
    out = feedback._redact(dangerous)
    assert "not included" in out
    assert "hunter2" not in out
    assert "correct horse" not in out


@pytest.mark.parametrize("token", [
    "sk-abcdefghij0123456789klmnopqrst",
    "AIzaSyD1234567890abcdefghijklmnopqrs",
    "ghp_abcdefghij0123456789klmnopqrstuvwx",
])
def test_a_key_is_substituted_without_losing_the_sentence(token):
    """
    Substituted rather than dropped, because a key embedded in an otherwise
    useful sentence should not cost the sentence.
    """
    out = feedback._redact(f"the thing broke, here is the key {token} if it helps")
    assert token not in out
    assert "***redacted***" in out
    assert "the thing broke" in out


def test_ordinary_feedback_survives_intact():
    """Redaction that eats real feedback has defeated the point."""
    said = "you sorted the wrong emails, I asked about machine learning"
    assert feedback._redact(said) == said


def test_the_summary_is_short_and_says_the_four_things(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "RATINGS_PATH", tmp_path / "ratings.jsonl")
    sent = {}

    def fake_mail(subject, body):
        sent["subject"], sent["body"] = subject, body
        return True, ""

    monkeypatch.setattr(feedback, "_mail", fake_mail)
    reply = feedback.record_rating(
        score=6, comment="you sorted the wrong emails",
        about="sort my machine learning emails",
        did="scan_inbox, draft_telegram_post",
    )

    assert "6/10" in sent["subject"]
    for heading in ("WHAT HE ASKED FOR", "WHAT JALEN DID",
                    "WHAT HE SAID ABOUT IT", "SCORE"):
        assert heading in sent["body"]
    assert len(sent["body"]) < 1200, "this is a summary, not a log dump"
    assert "noted" in reply.lower() or "thanks" in reply.lower()


def test_it_goes_to_the_address_he_gave():
    assert feedback.FEEDBACK_TO == "jaloliddin2009applicant@gmail.com"


def test_the_rating_is_kept_even_when_the_email_fails(tmp_path, monkeypatch):
    """
    Written to disk BEFORE the mail is attempted. Gmail can be down,
    unauthorised or simply offline, and a rating that only survives when the
    network was up is a rating that quietly stops existing.
    """
    path = tmp_path / "ratings.jsonl"
    monkeypatch.setattr(feedback, "RATINGS_PATH", path)
    monkeypatch.setattr(feedback, "_mail", lambda s, b: (False, "not connected"))

    reply = feedback.record_rating(score=3, comment="bad", about="a thing")
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["score"] == 3
    assert "couldn't email" in reply, "it should say the mail failed, not hide it"


def test_a_low_score_gets_a_different_acknowledgement(tmp_path, monkeypatch):
    """
    "Thanks, noted" to a 2 out of 10 reads as not listening.
    """
    monkeypatch.setattr(feedback, "RATINGS_PATH", tmp_path / "r.jsonl")
    monkeypatch.setattr(feedback, "_mail", lambda s, b: (True, ""))
    low = feedback.record_rating(score=2, comment="it did the wrong thing")
    assert "low scores" in low.lower() or "useful" in low.lower()


def test_history_reports_an_average(tmp_path, monkeypatch):
    path = tmp_path / "r.jsonl"
    monkeypatch.setattr(feedback, "RATINGS_PATH", path)
    monkeypatch.setattr(feedback, "_mail", lambda s, b: (True, ""))
    for score in (4, 8, 9):
        feedback.record_rating(score=score, about="something")
    out = feedback.rating_history()
    assert "7.0 out of 10" in out
    assert "3 rated jobs" in out
