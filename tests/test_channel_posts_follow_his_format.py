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
# His channel, as config/jalen.yaml names it among the chats he sends to without
# being asked: the check is for posts that go there (messaging._his_channel).
HIS_CHANNEL = "AI engineering & Machine learning"


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
    assert messaging._in_house_format(theirs, False, to=HIS_CHANNEL) == (theirs, "", "")
    assert messaging._in_house_format(LEE_LAB, False, to=HIS_CHANNEL)[2], (
        "the old handle passed for a new owner")


def test_the_brain_is_told_about_the_check():
    from jalen.brain.tools import TOOL_SPECS

    description, params = TOOL_SPECS["send_telegram_message"]
    assert "as_written" in params and "fix it and send again" in description
    description, params = TOOL_SPECS["save_telegram_draft"]
    assert "as_written" in params and "fix it and save the draft again" in description


# --------------------------------------------------------------------------
# 5. A DRAFT IS CHECKED TOO
#
# save_telegram_draft is the path the brain is told to use for a channel post
# ("compose it, save it here, and tell him it's waiting"), and it was the one
# path that skipped the check: the post he opened on his phone to press send
# could carry every mistake the send refuses.
# --------------------------------------------------------------------------
def draft(markup, **kwargs):
    return messaging.save_telegram_draft(to=HIS_CHANNEL, text=markup, **kwargs)


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_a_broken_post_is_not_left_as_a_draft(wired, name):
    breaks, says = BROKEN[name]
    reply = draft(breaks(LEE_LAB))
    assert reply.startswith("Nothing saved — the post doesn't follow your channel's format yet")
    assert says in reply
    assert wired.wire == [], f"{name}: a broken post was left in his draft box"


def test_a_refused_draft_says_save_it_again_never_send_it():
    """Told to "send it again", the brain would post what he asked to see first."""
    broken = BROKEN["no requirements"][0](LEE_LAB)
    reply = messaging._in_house_format(broken, False, "save the draft again", to=HIS_CHANNEL)[2]
    assert "save the draft again" in reply and "send" not in reply


def test_a_drafted_post_is_tidied_and_says_so(wired):
    reply = draft(LEE_LAB.replace(f"{BULLET} Passion in NLP", "- Passion in NLP"))
    assert reply.startswith("Saved as a draft in")
    assert reply.endswith(f"Tidied to the channel format: changed 1 bullet to {BULLET}.")
    kind, text, _entities = wired.wire[-1]
    assert kind == "draft" and f"{BULLET} Passion in NLP" in text


def test_his_real_post_is_drafted_word_for_word(wired):
    reply = draft(LEE_LAB)
    assert reply.startswith("Saved as a draft in") and "Tidied" not in reply
    assert wired.wire[-1][1] == plain_of(LEE_LAB)


def test_as_written_drafts_a_house_post_in_his_own_layout(wired):
    custom = BROKEN["no requirements"][0](LEE_LAB)
    assert draft(custom, as_written=True).startswith("Saved as a draft in")
    assert wired.wire[-1][1] == plain_of(custom)


def test_a_dm_reply_draft_is_not_a_channel_post(wired):
    assert draft("see you at six").startswith("Saved as a draft in")
    assert wired.wire[-1][1] == "see you at six"


# --------------------------------------------------------------------------
# 6. EVERY HEADING CARRIES ITS EMOJI
#
# "Emoji are punctuation, not decoration. One after the title, one after
# each section heading, one in the sign-off." The sign-off was checked; the
# rest was not - and the title check's own message asked for an emoji it
# never looked for. A heading that ends at its </b> gets the guide's emoji,
# and the reply says so; a heading with anything after it is a choice.
# --------------------------------------------------------------------------
PEN, ROCKET, ROAD, PIN, CLAP = (chr(0x1F58A), chr(0x1F680), chr(0x1F6E3),
                                chr(0x1F4CC), chr(0x1F44F))


def _bare(post, *headings):
    for heading, emoji in headings:
        post = post.replace(f"{heading} {emoji}", heading, 1)
    return post


def test_his_post_with_bare_headings_gets_each_emoji_back():
    bare = _bare(LEE_LAB, ("NLP students</b>", PEN), ("<b>Research Areas:</b>", ROAD),
                 ("<b>Requirements:</b>", CLAP))
    sent, tidied, wrong = check(bare)
    assert sent == LEE_LAB and wrong == []
    assert tidied == ["gave the title, Research Areas: and Requirements: their emoji"]


def test_a_project_title_gets_the_rocket_and_project_the_pin():
    project = (LEE_LAB.replace(f"<b>Research Areas:</b> {ROAD}", "<b>Project:</b>")
               .replace(f"NLP students</b> {PEN}", "NLP students</b>"))
    sent, tidied, wrong = check(project)
    assert wrong == []
    assert sent.splitlines()[0].endswith(f"</b> {ROCKET}")
    assert f"<b>Project:</b> {PIN}" in sent


@pytest.mark.parametrize("after", [
    '<tg-emoji emoji-id="5368324170671202288">' + CLAP + "</tg-emoji>",   # his premium one
    chr(0x1F525),                                                          # another emoji
    "(read this first)",                                                   # words
])
def test_a_heading_with_anything_after_it_is_left_as_he_wrote_it(after):
    chosen = LEE_LAB.replace(f"<b>Requirements:</b> {CLAP}", f"<b>Requirements:</b> {after}")
    sent, tidied, wrong = check(chosen)
    assert sent == chosen and tidied == []


def test_with_no_template_the_title_is_not_guessed_at():
    neither = _without(_bare(LEE_LAB, ("NLP students</b>", PEN)),
                       "<b>Research Areas:</b>", "<b>Requirements:</b>")
    sent, _tidied, wrong = check(neither)
    assert sent.splitlines()[0].endswith("</b>"), "an emoji was picked for an unknown template"
    assert any("Research Areas: section (a lab)" in w for w in wrong)


def test_the_reply_names_the_emoji_it_added(wired):
    reply = send(_bare(LEE_LAB, ("<b>Requirements:</b>", CLAP)))
    assert reply.endswith("Tidied to the channel format: gave Requirements: its emoji.")
    assert f"Requirements: {CLAP}" in wired.wire[-1][1]
