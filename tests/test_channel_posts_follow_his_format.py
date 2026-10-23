"""
"It should be able to ... write perfect telegram posts."   - his words, 2026-10-23

The house format for his channel is docs/community_post_format.md, and the
brain is handed it on every post. Nothing checked that a post FOLLOWED it, so
"- " bullets, a Q&A block that is not expandable, two questions instead of
three, or a sign-off in other words went out under his name.

Now send_telegram_message checks a house post first (jalen/tools/post_format.py):
  * typing slips - bullets, **bold**, the expandable block, how the questions
    are numbered - are put right, and the reply says what was changed;
  * structural mistakes - a missing section, the wrong questions, a rewritten
    sign-off - send NOTHING, and the reply says exactly what to fix;
  * anything that is not a house post goes exactly as written, and so does a
    house post he asked for as_written.

Built from his real Lee Language Lab post, the worked example in the format
guide itself, so the check and the guide cannot disagree about what is right.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jalen.tools import messaging, post_format
from jalen.tools.post_format import BULLET, QUESTIONS, SIGN_OFF, SUB_ITEM

from test_telegram_posting import FakeClient, _kinds, wired  # noqa: F401 - fixture

ROOT = Path(__file__).resolve().parent.parent
GUIDE = (ROOT / "docs" / "community_post_format.md").read_text(encoding="utf-8")
LEE_LAB = GUIDE.split("## Worked example")[1].split("```")[1].strip("\n")


def plain_of(markup):
    return messaging._formatted(markup).text


def check(markup):
    return post_format.check(markup, plain_of)


def send(markup, **kwargs):
    return messaging.send_telegram_message(to="AI engineering & Machine learning",
                                           text=markup, **kwargs)


# --------------------------------------------------------------------------
# 1. HIS OWN POST IS RIGHT, AND IS LEFT ALONE
# --------------------------------------------------------------------------
def test_his_real_lab_post_passes_untouched():
    sent, tidied, wrong = check(LEE_LAB)
    assert (sent, tidied, wrong) == (LEE_LAB, [], [])


def test_his_real_post_goes_out_word_for_word(wired):
    reply = send(LEE_LAB)
    assert reply.startswith("Sent to") and "Tidied" not in reply
    _kind, text, entities = wired.wire[-1]
    assert text == plain_of(LEE_LAB)
    quotes = [e for e in entities if type(e).__name__ == "MessageEntityBlockquote"]
    assert quotes and quotes[0].collapsed is True


def test_a_project_post_in_template_b_passes():
    project = (LEE_LAB.replace("<b>Research Areas:</b>", "<b>Project:</b>")
               .replace("Lab Opportunity for NLP students", "Project with the Edge AI Lab"))
    assert check(project)[2] == []


def test_the_fixed_lines_in_the_check_are_the_guides_own_words():
    """If the guide changes a fixed line, this fails instead of the two drifting apart."""
    for line in (SIGN_OFF, post_format.COMPETENT, post_format.REACTION,
                 post_format.QA_HEADING, post_format.DEFAULT_HANDLE, *QUESTIONS):
        assert line in GUIDE, line


# --------------------------------------------------------------------------
# 2. TYPING SLIPS ARE PUT RIGHT, AND SAID
# --------------------------------------------------------------------------
def test_hyphen_and_asterisk_bullets_become_his_bullet():
    slipped = LEE_LAB.replace(f"{BULLET} Passion in NLP", "- Passion in NLP").replace(
        f"{BULLET} Devotion of", "* Devotion of")
    sent, tidied, wrong = check(slipped)
    assert sent == LEE_LAB and wrong == []
    assert tidied == [f"changed 2 bullets to {BULLET}"]


def test_markdown_bold_becomes_real_bold():
    slipped = LEE_LAB.replace("<b>Prof. Annie En-Shiun Lee</b>", "**Prof. Annie En-Shiun Lee**")
    sent, tidied, wrong = check(slipped)
    assert sent == LEE_LAB and wrong == [] and tidied == ["turned 1 **bold** into real bold"]


def test_a_q_and_a_block_that_would_show_open_is_made_expandable():
    slipped = LEE_LAB.replace("<blockquote expandable>", "<blockquote>")
    sent, tidied, wrong = check(slipped)
    assert sent == LEE_LAB and wrong == [] and tidied == ["made the Q&A block expandable"]


def test_questions_numbered_like_a_list_get_his_bold_digits():
    slipped = (LEE_LAB.replace("<b>1</b> What will you get?", "1. What will you get?")
               .replace("<b>3</b> How are applicants selected?", "Question 3: How are applicants selected?"))
    sent, tidied, wrong = check(slipped)
    assert sent == LEE_LAB and wrong == []
    assert tidied == ["numbered the questions with bold digits"]


def test_bullets_inside_the_q_and_a_become_his_dash():
    slipped = LEE_LAB.replace(f"{SUB_ITEM} GPA", "- GPA").replace(f"{SUB_ITEM} Resume", f"{BULLET} Resume")
    sent, tidied, wrong = check(slipped)
    assert sent == LEE_LAB and wrong == [] and tidied == [f"changed 2 Q&A items to {SUB_ITEM}"]


def test_the_reply_says_what_was_tidied_after_the_post(wired):
    reply = send(LEE_LAB.replace(f"{BULLET} Passion in NLP", "- Passion in NLP"))
    assert reply.startswith("Sent to")
    assert reply.endswith(f"Tidied to the channel format: changed 1 bullet to {BULLET}.")
    assert f"{BULLET} Passion in NLP" in wired.wire[-1][1]


# --------------------------------------------------------------------------
# 3. STRUCTURAL MISTAKES SEND NOTHING AND SAY WHAT TO FIX
# --------------------------------------------------------------------------
def _without(markup, start, end):
    a = markup.index(start)
    return markup[:a] + markup[markup.index(end, a):]


BROKEN = {
    "no requirements": (lambda p: _without(p, "<b>Requirements:</b>", "<blockquote"),
                        "own bold Requirements: section"),
    "no middle section": (lambda p: _without(p, "<b>Research Areas:</b>", "<b>Requirements:</b>"),
                          "Research Areas: section (a lab)"),
    "both middle sections": (lambda p: p.replace("<b>Requirements:</b>",
                                                 "<b>Project:</b>\n\n<b>Requirements:</b>"),
                             "both a Research Areas: and a Project: section"),
    "a different question": (lambda p: p.replace("What will you get?", "What are the benefits?"),
                             "question 1 must be exactly"),
    "a fourth question": (lambda p: p.replace("</blockquote>",
                                              "\n<b>4</b> Is it paid?\nNo.\n</blockquote>"),
                          "never a fourth"),
    "the sign-off rewritten": (lambda p: p.replace(SIGN_OFF, "DM me if interested! 👇"),
                               "the last two lines must be exactly"),
    "another handle": (lambda p: p.replace("@Iht_student", "@someone_else"),
                       "the last two lines must be exactly"),
    "no competent line": (lambda p: p.replace("Students who are competent in:", "You know:"),
                          "Students who are competent in:"),
    "a plain title": (lambda p: p.replace("<b>Lab Opportunity for NLP students</b>",
                                          "Lab Opportunity for NLP students", 1),
                      "the title in bold"),
    "no reaction line": (lambda p: p.replace(f"{BULLET} Leave a reaction to this post\n", ""),
                         "Leave a reaction to this post"),
    "no q and a": (lambda p: _without(p, "<blockquote", "Interested?"),
                   "<blockquote expandable> that opens with"),
}


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_a_structural_mistake_is_named(name):
    breaks, says = BROKEN[name]
    wrong = check(breaks(LEE_LAB))[2]
    assert any(says in problem for problem in wrong), (name, wrong)


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_a_structural_mistake_never_reaches_the_channel(wired, name):
    breaks, says = BROKEN[name]
    reply = send(breaks(LEE_LAB))
    assert reply.startswith("Nothing sent — the post doesn't follow your channel's format yet")
    assert says in reply and "send it again" in reply
    assert wired.wire == [], f"{name}: a broken post reached his channel"


def test_a_broken_post_in_a_batch_is_reported_not_sent(wired):
    from jalen.tools import drafting

    broken = BROKEN["no requirements"][0](LEE_LAB)
    reply = drafting.send_posts(to="AI engineering & Machine learning", posts=[LEE_LAB, broken])
    assert reply.startswith("1 of 2 sent")
    assert len(wired.wire) == 1


# --------------------------------------------------------------------------
# 4. WHAT IS NOT CHECKED
# --------------------------------------------------------------------------
@pytest.mark.parametrize("message", [
    "Hi Rodion, see you at six",
    "<b>Reminder</b>: the meetup is tomorrow at 18:00",
    "- one\n- two\n- three",
    "Requirements: bring a laptop",
])
def test_anything_that_is_not_a_house_post_goes_as_written(wired, message):
    reply = send(message)
    assert reply.startswith("Sent to") and "Tidied" not in reply
    assert wired.wire[-1][1] == plain_of(message)


def test_as_written_sends_a_house_post_in_his_own_layout(wired):
    custom = BROKEN["no requirements"][0](LEE_LAB)
    reply = send(custom, as_written=True)
    assert reply.startswith("Sent to")
    assert wired.wire[-1][1] == plain_of(custom)


def test_premium_emoji_in_the_sign_off_still_match_it():
    premium = LEE_LAB.replace("dm me 👇:", 'dm me <tg-emoji emoji-id="5368324170671202288">👇</tg-emoji>:')
    assert check(premium)[2] == []


def test_the_sign_off_handle_is_his_configured_one(monkeypatch):
    from jalen.config import CONFIG

    real = CONFIG.get_path
    monkeypatch.setattr(CONFIG, "get_path", lambda key, default=None:
                        "@another_channel_owner" if key == "personal.channel_handle"
                        else real(key, default))
    theirs = LEE_LAB.replace("@Iht_student", "@another_channel_owner")
    assert messaging._in_house_format(theirs, False) == (theirs, "", "")
    assert messaging._in_house_format(LEE_LAB, False)[2], "the old handle passed for a new owner"


def test_the_brain_is_told_about_the_check():
    from jalen.brain.tools import TOOL_SPECS

    description, params = TOOL_SPECS["send_telegram_message"]
    assert "as_written" in params and "fix it and send again" in description
