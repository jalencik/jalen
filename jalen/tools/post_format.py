"""
His channel's post format, checked - and where it is only typing, fixed -
before a post goes out.

    "it should be able to ... write perfect telegram posts"   - 2026-10-23

The format is docs/community_post_format.md, and the model is handed it on
every post (voice.community_post_guide). Nothing checked that what it wrote
FOLLOWED it: a post with "- " bullets, a Q&A block that is not expandable,
two questions instead of three, or a sign-off in other words reached his
channel, under his name, in front of the people he wants to apply.

TWO KINDS OF MISTAKE, TREATED DIFFERENTLY
------------------------------------------
TYPING - a bullet that is "-" instead of "•", "**bold**", a Q&A
<blockquote> without `expandable`, a question numbered "1." instead of a
bold digit. There is exactly one right answer, so it is put right (`tidy`)
and the reply says what was changed.

STRUCTURE - a section missing, the wrong questions, the sign-off rewritten,
the title not bold. There is no single right answer, so nothing is sent and
the reply says exactly what to fix (`problems`); the brain fixes it and
sends again.

Only a post that IS a house post is checked: one with the Q&A heading, or
with "Requirements:" and the sign-off's "dm me". Everything else he sends -
a reply, a note, an announcement in another shape - goes as written. And
send_telegram_message(as_written=True) sends a post exactly as written, for
when he asks for a different layout on purpose.
"""
from __future__ import annotations

import re

# The three questions, in this order, always (the format's own words: "Not
# two, not four, and not different questions").
QUESTIONS = ("What will you get?", "Who should apply?", "How are applicants selected?")
# How question 2's answer opens, word for word: "That phrasing is his."
COMPETENT = "Students who are competent in:"
# The first of the two lines that never change. The second is his handle.
SIGN_OFF = "Interested? Don't hesitate to dm me 👇:"
DEFAULT_HANDLE = "@Iht_student"
QA_HEADING = "Most Asked Questions"
REACTION = "Leave a reaction to this post"

BULLET = "•"
SUB_ITEM = "—"
# What a bullet gets typed as instead: hyphen, asterisk, middle dot, en dash.
_WRONG_BULLET = re.compile(r"^(\s*)(?:[-*·–])\s+", re.M)
# The Q&A's sub-items: any of those, or the post's own bullet, in the wrong place.
_WRONG_SUB_ITEM = re.compile(r"^(\s*)(?:[-*·–•])\s+", re.M)
_MARKDOWN_BOLD = re.compile(r"\*\*(\S(?:.*?\S)?)\*\*")
_PLAIN_BLOCKQUOTE = re.compile(r"<blockquote\s*>")
_ANY_BLOCKQUOTE = re.compile(r"<blockquote(?:\s+expandable)?\s*>")
# "1. What will you get?", "1) ...", "Question 1: ...", "<b>1.</b> ..."
_NUMBERED = re.compile(
    r"^(\s*)(?:<b>\s*)?(?:question\s*)?([123])\s*[.):]?\s*(?:</b>)?\s*(?=\S)", re.I | re.M)


def is_house_post(markup: str) -> bool:
    """A post in the house format, by its own fixed parts."""
    text = markup or ""
    return QA_HEADING.lower() in text.lower() or (
        "requirements:" in text.lower() and "dm me" in text.lower())


def _split_qa(markup: str) -> tuple[str, str, str]:
    """(before the Q&A block, the block with its tags, after it)."""
    opened = _ANY_BLOCKQUOTE.search(markup)
    if not opened:
        return markup, "", ""
    closed = markup.find("</blockquote>", opened.end())
    if closed < 0:
        return markup, "", ""
    end = closed + len("</blockquote>")
    return markup[:opened.start()], markup[opened.start():end], markup[end:]


# The emoji a heading carries - the guide's table ("one after the title, one
# after each section heading"), by code point and with no presentation
# selector, exactly as they are in his real post.
_TITLE_EMOJI = {"lab": chr(0x1F58A), "project": chr(0x1F680)}        # pen, rocket
_HEADING_EMOJI = {
    "<b>Research Areas:</b>": chr(0x1F6E3),                            # motorway
    "<b>Project:</b>": chr(0x1F4CC),                                   # pushpin
    "<b>Requirements:</b>": chr(0x1F44F),                              # clapping hands
}


def _bare_headings(part: str) -> tuple[str, list[str]]:
    """
    Give the title, and each section heading, the emoji the guide gives it,
    when the line ends at its </b> with nothing after it. Returns the part
    and the names of what was given one.

    Only a BARE heading. One with anything after it - its emoji, a premium
    one, another emoji, a word - is a choice and is left alone. The title's
    emoji depends on the template, so it is given only when the post has
    exactly one of Research Areas: (a lab, pen) and Project: (a project,
    rocket); with both or neither, problems() says what to fix instead.
    """
    lab = "<b>Research Areas:</b>" in part
    project = "<b>Project:</b>" in part
    template = "lab" if lab and not project else "project" if project and not lab else ""
    named: list[str] = []
    out: list[str] = []
    past_title = False
    for line in part.splitlines(keepends=True):
        body = line.rstrip()
        ending = line[len(body):]
        heading = body.strip()
        if heading in _HEADING_EMOJI:
            out.append(f"{body} {_HEADING_EMOJI[heading]}{ending}")
            named.append(heading[len("<b>"):-len("</b>")])
        elif (not past_title and template and heading.startswith("<b>")
              and heading.endswith("</b>")):
            out.append(f"{body} {_TITLE_EMOJI[template]}{ending}")
            named.append("the title")
        else:
            out.append(line)
        past_title = past_title or bool(heading)
    return "".join(out), named


def tidy(markup: str) -> tuple[str, list[str]]:
    """
    Put right what is only typing, and say what was put right. Returns the
    post as it should go out and a list of short sentences, one per kind of
    change, for the reply.
    """
    changes: list[str] = []
    text = markup or ""

    bolded, n = _MARKDOWN_BOLD.subn(r"<b>\1</b>", text)
    if n:
        text = bolded
        changes.append(f"turned {n} **bold** into real bold")

    before, block, after = _split_qa(text)
    if block and QA_HEADING.lower() in block.lower() and _PLAIN_BLOCKQUOTE.match(block):
        block = _PLAIN_BLOCKQUOTE.sub("<blockquote expandable>", block, count=1)
        changes.append("made the Q&A block expandable")

    outside = 0
    fixed_parts = []
    for part in (before, after):
        fixed, count = _WRONG_BULLET.subn(rf"\g<1>{BULLET} ", part)
        fixed_parts.append(fixed)
        outside += count
    before, after = fixed_parts
    if outside:
        changes.append(f"changed {outside} bullet{'s' if outside != 1 else ''} to {BULLET}")

    before, given = _bare_headings(before)
    if given:
        listed = given[0] if len(given) == 1 else ", ".join(given[:-1]) + " and " + given[-1]
        changes.append(f"gave {listed} {'its' if len(given) == 1 else 'their'} emoji")

    if block:
        numbered, renumbered = 0, block
        for question_number, question in enumerate(QUESTIONS, start=1):
            pattern = re.compile(
                rf"^(\s*)(?:<b>\s*)?(?:question\s*)?{question_number}\s*[.):]?\s*(?:</b>)?\s*"
                rf"(?:<b>)?\s*{re.escape(question)}\s*(?:</b>)?\s*$", re.I | re.M)
            wanted = f"<b>{question_number}</b> {question}"
            renumbered, count = pattern.subn(rf"\g<1>{wanted}", renumbered)
            numbered += count if count and wanted not in block else 0
        if numbered:
            changes.append("numbered the questions with bold digits")
        block = renumbered

        inner_start = block.find(">") + 1
        inner_end = block.rfind("</blockquote>")
        inner = block[inner_start:inner_end]
        fixed, subs = _WRONG_SUB_ITEM.subn(rf"\g<1>{SUB_ITEM} ", inner)
        if subs:
            block = block[:inner_start] + fixed + block[inner_end:]
            changes.append(f"changed {subs} Q&A item{'s' if subs != 1 else ''} to {SUB_ITEM}")

    return before + block + after, changes


def _lines(plain: str) -> list[str]:
    return [line.strip() for line in (plain or "").splitlines() if line.strip()]


def problems(markup: str, plain: str, handle: str = DEFAULT_HANDLE) -> list[str]:
    """
    What is wrong with the STRUCTURE of a house post, one instruction each,
    or [] when it follows the format. `markup` is the post as written,
    `plain` the same post as Telegram will show it (tags read, premium
    emoji as their ordinary emoji).
    """
    wrong: list[str] = []
    markup = markup or ""
    lines = _lines(plain)
    marked = [line.strip() for line in markup.splitlines() if line.strip()]

    if not marked or not marked[0].startswith("<b>") or "</b>" not in marked[0]:
        wrong.append("the first line must be the title in bold, followed by its emoji")

    lab = "<b>Research Areas:</b>" in markup
    project = "<b>Project:</b>" in markup
    if lab and project:
        wrong.append("it has both a Research Areas: and a Project: section - a lab post "
                     "takes Research Areas:, a project or programme takes Project:, never both")
    elif not (lab or project):
        wrong.append("it needs a bold Research Areas: section (a lab) or a bold Project: "
                     "section (a project, programme or mentorship)")

    requirements = markup.find("<b>Requirements:</b>")
    if requirements < 0:
        wrong.append("it needs its own bold Requirements: section")
    else:
        middle = max(markup.find("<b>Research Areas:</b>"), markup.find("<b>Project:</b>"))
        if middle > requirements:
            wrong.append("Requirements: comes after the Research Areas: or Project: section")
        if REACTION.lower() not in markup[requirements:].lower():
            wrong.append(f"Requirements: ends with the line \"{REACTION}\"")

    before, block, _after = _split_qa(markup)
    if not block:
        wrong.append(f"the three questions go in a <blockquote expandable> that opens with "
                     f"<b>{QA_HEADING}</b>")
    else:
        if not block.startswith("<blockquote expandable"):
            wrong.append("the Q&A block must be <blockquote expandable>, or it shows open in "
                         "the feed as a wall of text")
        if f"<b>{QA_HEADING}</b>" not in block:
            wrong.append(f"the Q&A block opens with <b>{QA_HEADING}</b>")
        found, at = [], 0
        for number, question in enumerate(QUESTIONS, start=1):
            spot = block.find(f"<b>{number}</b> {question}", at)
            if spot < 0:
                wrong.append(f"question {number} must be exactly \"<b>{number}</b> {question}\"")
            else:
                found.append(number)
                at = spot
        if "<b>4</b>" in block:
            wrong.append("there are exactly three questions, never a fourth")
        second = block.find(f"<b>2</b> {QUESTIONS[1]}")
        if second >= 0 and COMPETENT not in block[second:]:
            wrong.append(f"question 2's answer opens with the line \"{COMPETENT}\"")
        if requirements >= 0 and markup.find(block) < requirements:
            wrong.append("the Q&A block comes after Requirements:, not before it")

    signature = [SIGN_OFF, handle]
    if lines[-2:] != signature:
        wrong.append(f"the last two lines must be exactly \"{SIGN_OFF}\" and \"{handle}\"")

    if _WRONG_BULLET.search(before) or "**" in markup:
        wrong.append(f"bullets are {BULLET} and bold is <b>...</b>")
    return wrong


def check(markup: str, plain_of, handle: str = DEFAULT_HANDLE) -> tuple[str, list[str], list[str]]:
    """
    The whole check for one post: (what to send, what was tidied, what is
    still wrong). `plain_of` turns markup into the words Telegram will show
    (messaging._formatted(...).text), so the structure is judged on the
    post as it will be READ, premium emoji and all.
    """
    if not is_house_post(markup):
        return markup, [], []
    tidied, changes = tidy(markup)
    return tidied, changes, problems(tidied, plain_of(tidied), handle)
