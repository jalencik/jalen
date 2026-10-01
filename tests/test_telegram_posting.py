"""
What actually reaches Telegram when Jalen posts, and what he is told after.

Three defects found in data/audit.jsonl, all on the path he uses most:

1. send_telegram_message handed the text to Telethon with its DEFAULT parse
   mode, which is markdown. The post format he is told to write is Telegram
   HTML, so <b>, <blockquote expandable> and &amp; went out as literal
   characters - 9 of 12 real post sends. He called them "mathematical
   hieroglyphs". save_telegram_draft never had this bug because it parsed
   the HTML itself; the send path simply never did.

2. Saved Messages was named by his username, so the brain read "Sent to
   Iht_student", believed the post had gone to the wrong chat, and (row 4824)
   drafted it again and then sent it again.

3. read_telegram rendered oldest-first and clipped the first 3000 characters,
   so the NEWEST message - the one he means by "read what I just posted" -
   was the part cut off. A real post is about 2000 characters.

Nothing here touches a network. The fake client below imitates Telethon's
own parse-mode rule (formatting_entities=None means "parse with parse_mode,
and parse_mode=() means the client default, which is markdown"), so these
tests check what Telegram would have RECEIVED, not which arguments were used.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jarvis.tools import messaging


# ---------------------------------------------------------------- the fakes
def _telethon_parse(text, parse_mode, formatting_entities):
    """Telethon 1.44's rule, from client/messageparse.py:_parse_message_text."""
    from telethon import utils

    if formatting_entities is not None:
        return text, list(formatting_entities)
    mode = "md" if parse_mode == () else parse_mode   # the client default
    mode = utils.sanitize_parse_mode(mode)
    if not mode:
        return text, []
    return mode.parse(text)


class FakeChannel:
    def __init__(self, title="AI engineering & Machine learning", id=1):
        self.title = title
        self.id = id


class FakeUser:
    """His own account as Telethon returns it: username set, is_self True."""

    def __init__(self, id=42, username="Iht_student", first_name="Jaloliddin",
                 is_self=True):
        self.id = id
        self.username = username
        self.first_name = first_name
        self.is_self = is_self


class FakeMe:
    def __init__(self, user_id=42):
        self.user_id = user_id
        self.id = user_id


class FakeClient:
    """Records what Telegram would have received, and sends nothing."""

    def __init__(self, entity=None, reject_entities_once=False, fail_with=None):
        self.entity = entity if entity is not None else FakeChannel()
        self.wire = []          # (kind, text, entities) as Telegram sees it
        self.requests = []
        self.reject_entities_once = reject_entities_once
        self.fail_with = fail_with      # raised on EVERY send, e.g. a timeout
        self.attempts = 0

    def _maybe_reject(self, entities):
        self.attempts += 1
        if self.fail_with is not None:
            raise self.fail_with
        if self.reject_entities_once and entities:
            self.reject_entities_once = False
            from telethon.errors import BadRequestError

            raise BadRequestError(request=None, message="ENTITY_BOUNDS_INVALID")

    async def send_message(self, entity, message="", *, parse_mode=(),
                           formatting_entities=None, **_):
        text, entities = _telethon_parse(message, parse_mode, formatting_entities)
        if not text:
            # Telethon 1.44, client/messages.py:907, before any request.
            raise ValueError("The message cannot be empty unless a file is provided")
        self._maybe_reject(entities)
        self.wire.append(("message", text, entities))

    async def send_file(self, entity, file, *, caption=None, parse_mode=(),
                        formatting_entities=None, **_):
        # Telethon's send_file treats an EMPTY entity list as "parse it".
        caption = caption or ""
        if formatting_entities:
            text, entities = caption, list(formatting_entities)
        else:
            text, entities = _telethon_parse(caption, parse_mode, None)
        self._maybe_reject(entities)
        self.wire.append(("file", text, entities))

    async def __call__(self, request):
        self.requests.append(request)
        self.wire.append(("draft", request.message, list(request.entities or [])))
        return True

    async def get_me(self, input_peer=False):
        return FakeMe()


def _run(work, client):
    return asyncio.new_event_loop().run_until_complete(work(client))


@pytest.fixture
def wired(monkeypatch):
    client = FakeClient()

    async def fake_resolve(_client, name):
        return client.entity

    monkeypatch.setattr(messaging, "_resolve", fake_resolve)
    monkeypatch.setattr(messaging, "_enabled", lambda: None)
    monkeypatch.setattr(messaging.RUNTIME, "run", lambda work, **_: _run(work, client))
    return client


POST = (
    "<b>Lab Opportunity for NLP students</b> \U0001f58a\n\n"
    "A new opening with the <b>Lee Language Lab</b> - Q&amp;A below.\n\n"
    "<b>Requirements:</b>\n\n"
    "• Passion in NLP\n\n"
    "<blockquote expandable><b>Most Asked Questions</b>\n\n"
    "<b>1</b> What will you get?\n— Real research experience\n</blockquote>\n\n"
    "Interested? Don't hesitate to dm me \U0001f447:\n@Iht_student"
)


def _kinds(entities):
    return {type(e).__name__ for e in entities}


# ------------------------------------------------------------- 1. HTML sends
def test_a_sent_post_arrives_formatted_not_as_hieroglyphs(wired):
    messaging.send_telegram_message(to="ML community", text=POST)

    kind, text, entities = wired.wire[-1]
    assert kind == "message"
    assert "<b>" not in text and "<blockquote" not in text, (
        "the tags reached Telegram as literal characters"
    )
    assert "&amp;" not in text and "Q&A below" in text
    assert "MessageEntityBold" in _kinds(entities)
    quotes = [e for e in entities if type(e).__name__ == "MessageEntityBlockquote"]
    assert quotes and quotes[0].collapsed is True, "the Q&A block is not expandable"


def test_plain_text_is_sent_exactly_as_written(wired):
    """
    A router literal, or anything he dictates, is not markup. Markdown would
    eat the asterisks; HTML would eat a stray angle bracket. Neither may.
    """
    literal = "**not bold** and a < b & c, 2 * 3 = 6 _really_"
    messaging.send_telegram_message(to="ML community", text=literal)

    _, text, entities = wired.wire[-1]
    assert text == literal
    assert entities == []


def test_an_unpaired_tag_is_left_alone(wired):
    literal = "use <b> to make things bold"
    messaging.send_telegram_message(to="ML community", text=literal)
    assert wired.wire[-1][1] == literal


def test_markup_that_will_not_parse_goes_out_plain_and_says_so(wired, monkeypatch):
    def broken(_text):
        raise ValueError("malformed")

    monkeypatch.setattr(messaging, "_parse_html", broken)
    result = messaging.send_telegram_message(to="ML community", text=POST)

    _, text, entities = wired.wire[-1]
    assert "<b>" not in text and "</blockquote>" not in text, "tags were not stripped"
    assert "Lab Opportunity for NLP students" in text and "Q&A below" in text
    assert entities == []
    assert "without formatting" in result.lower()


def test_telegram_rejecting_the_entities_retries_plain_and_says_so(monkeypatch):
    client = FakeClient(reject_entities_once=True)

    async def fake_resolve(_client, name):
        return client.entity

    monkeypatch.setattr(messaging, "_resolve", fake_resolve)
    monkeypatch.setattr(messaging, "_enabled", lambda: None)
    monkeypatch.setattr(messaging.RUNTIME, "run", lambda work, **_: _run(work, client))

    result = messaging.send_telegram_message(to="ML community", text=POST)
    assert len(client.wire) == 1, "a rejected request delivered nothing; it must retry once"
    _, text, entities = client.wire[0]
    assert "<b>" not in text and entities == []
    assert "without formatting" in result.lower()
    # send_posts counts a reply containing these words as a FAILURE. This
    # one went out, so it must not read as one.
    for word in ("couldn't", "could not", "nothing sent"):
        assert word not in result.lower()


def test_the_spoken_reply_does_not_read_tags_aloud(wired):
    result = messaging.send_telegram_message(to="ML community", text=POST)
    assert "<b>" not in result and "<blockquote" not in result


def test_a_draft_that_will_not_parse_is_saved_without_the_tags(wired, monkeypatch):
    """
    The draft path's old fallback saved the RAW text, tags and all - the
    exact hieroglyphs the parse was there to prevent.
    """
    monkeypatch.setattr(
        messaging, "_parse_html", lambda _t: (_ for _ in ()).throw(ValueError("x"))
    )
    result = messaging.save_telegram_draft(to="ML community", text=POST)

    _, text, entities = wired.wire[-1]
    assert "<b>" not in text and "&amp;" not in text
    assert "Lab Opportunity for NLP students" in text
    assert "without formatting" in result.lower()


def test_a_plain_draft_keeps_its_literal_characters(wired):
    literal = "**draft** a < b & c"
    messaging.save_telegram_draft(to="ML community", text=literal)
    assert wired.wire[-1][1] == literal


def test_a_file_caption_arrives_formatted(wired, monkeypatch, tmp_path):
    from jarvis.tools import attachments

    doc = tmp_path / "poster.pdf"
    doc.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(attachments, "_resolve", lambda _name: (doc, ""))

    attachments.send_telegram_file(
        to="ML community", file="poster", caption="<b>Poster</b> for Q&amp;A"
    )
    kind, text, entities = wired.wire[-1]
    assert kind == "file"
    assert text == "Poster for Q&A"
    assert "MessageEntityBold" in _kinds(entities)


def test_a_plain_caption_is_not_markdown_mangled(wired, monkeypatch, tmp_path):
    from jarvis.tools import attachments

    doc = tmp_path / "poster.pdf"
    doc.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(attachments, "_resolve", lambda _name: (doc, ""))

    attachments.send_telegram_file(to="ML community", file="poster", caption="**v2** final")
    assert wired.wire[-1][1] == "**v2** final"


def test_send_posts_inherits_the_html_fix(wired):
    from jarvis.tools import drafting

    result = drafting.send_posts(to="ML community", posts=[POST, "plain second post"])
    assert result.startswith("2 of 2 sent")
    first, second = wired.wire[-2], wired.wire[-1]
    assert "<b>" not in first[1] and "MessageEntityBold" in _kinds(first[2])
    assert second[1] == "plain second post"


# ------------------------------------------------------- 2. Saved Messages
def test_saved_messages_is_called_saved_messages_not_his_username(wired):
    wired.entity = FakeUser()

    sent = messaging.send_telegram_message(to="saved messages", text="test")
    drafted = messaging.save_telegram_draft(to="saved messages", text="test")
    for reply in (sent, drafted):
        assert "Saved Messages" in reply
        assert "Iht_student" not in reply, (
            "the brain read his username as a different chat and sent again"
        )


def test_saved_messages_is_recognised_by_id_when_is_self_is_missing(wired):
    wired.entity = FakeUser(is_self=None)      # same id as get_me()
    reply = messaging.send_telegram_message(to="me", text="test")
    assert "Saved Messages" in reply and "Iht_student" not in reply


def test_another_person_is_still_named_as_themselves(wired):
    wired.entity = FakeUser(id=7, username="uluhbek", first_name="Uluhbek", is_self=False)
    reply = messaging.send_telegram_message(to="Uluhbek", text="hi")
    assert "Saved Messages" not in reply and "uluhbek" in reply


def test_a_file_to_saved_messages_says_saved_messages(wired, monkeypatch, tmp_path):
    from jarvis.tools import attachments

    doc = tmp_path / "cv.pdf"
    doc.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(attachments, "_resolve", lambda _name: (doc, ""))
    wired.entity = FakeUser()

    reply = attachments.send_telegram_file(to="me", file="cv")
    assert "Saved Messages" in reply and "Iht_student" not in reply


# ------------------------------------------------ 3. reading the newest post
class FakeMessage:
    def __init__(self, text, minute, out=True):
        self.text = text
        self.out = out
        self.sender = None
        self.date = datetime(2026, 9, 30, 10, minute, tzinfo=timezone.utc)


def _reading_client(monkeypatch, messages_newest_first, entity=None):
    client = FakeClient(entity=entity or FakeUser())

    async def get_messages(_entity, limit=15):
        return messages_newest_first[:limit]

    client.get_messages = get_messages

    async def fake_resolve(_client, name):
        return client.entity

    monkeypatch.setattr(messaging, "_resolve", fake_resolve)
    monkeypatch.setattr(messaging, "_enabled", lambda: None)
    monkeypatch.setattr(messaging.RUNTIME, "run", lambda work, **_: _run(work, client))
    return client


def test_the_newest_post_is_read_whole(monkeypatch):
    from jarvis import taint

    taint.he_asked_again()
    oldest = FakeMessage("OLDEST-START " + "o" * 1990 + " OLDEST-END", 1)
    middle = FakeMessage("MIDDLE-START " + "m" * 1990 + " MIDDLE-END", 2)
    newest = FakeMessage("NEWEST-START " + "n" * 1990 + " NEWEST-END", 3)
    _reading_client(monkeypatch, [newest, middle, oldest])   # Telethon: newest first

    result = messaging.read_telegram(chat="saved messages")

    assert "NEWEST-START" in result and "NEWEST-END" in result, (
        "the message he means was the part cut off"
    )
    assert "OLDEST-END" not in result, "the 3000-character budget was not kept"
    assert "older" in result.lower(), "it did not say that older messages were cut"
    assert "BEGIN UNTRUSTED CONTENT" in result and "END UNTRUSTED CONTENT" in result
    assert taint.origin_now() == "content", "the fence no longer taints the turn"
    taint.he_asked_again()


def test_a_short_chat_is_read_complete_and_in_order(monkeypatch):
    from jarvis import taint

    taint.he_asked_again()
    msgs = [FakeMessage("third", 3), FakeMessage("second", 2), FakeMessage("first", 1)]
    _reading_client(monkeypatch, msgs, entity=FakeChannel())

    result = messaging.read_telegram(chat="ML community")
    assert result.index("first") < result.index("second") < result.index("third")
    assert "older" not in result.lower()
    taint.he_asked_again()


def test_reading_saved_messages_names_it_saved_messages(monkeypatch):
    from jarvis import taint

    taint.he_asked_again()
    _reading_client(monkeypatch, [FakeMessage("note to self", 1)])
    result = messaging.read_telegram(chat="me")
    assert "Saved Messages" in result and "Iht_student" not in result
    taint.he_asked_again()


# ============================================================================
# The review of the fix above. Each block is one finding, reproduced first.
# ============================================================================
def _wire_client(monkeypatch, client):
    async def fake_resolve(_client, name):
        return client.entity

    monkeypatch.setattr(messaging, "_resolve", fake_resolve)
    monkeypatch.setattr(messaging, "_enabled", lambda: None)
    monkeypatch.setattr(messaging.RUNTIME, "run", lambda work, **_: _run(work, client))
    return client


def _a_poster(monkeypatch, tmp_path):
    from jarvis.tools import attachments

    doc = tmp_path / "poster.pdf"
    doc.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(attachments, "_resolve", lambda _name: (doc, ""))
    return attachments


# ---------------------------------- 4. no character of his text is ever lost
# Telethon's html.parse feeds Python's HTMLParser and never calls close(). The
# parser holds back everything after the last tag while an "&" in the final
# 34 characters has no space or ";" after it - a link's "&entry=12" - and
# that text simply never comes out. It also reads "&not" in "x&notice" as the
# entity for "¬", keeps a lone trailing "<" waiting for a tag, and swallows
# anything shaped like an HTML comment. None of it raised; all of it was
# reported "Sent to ...".
LOSSLESS = [
    ("<b>Lab Opportunity</b>\n\nDeadline Friday. Apply here: "
     "https://forms.gle/abc?usp=sf_link&entry=12",
     "Lab Opportunity\n\nDeadline Friday. Apply here: "
     "https://forms.gle/abc?usp=sf_link&entry=12"),
    ("<b>Q&amp;A</b> with AT&T", "Q&A with AT&T"),
    ("<b>Note</b> x&notice y", "Note x&notice y"),
    ("<b>Fees</b> &copy2024 and &para", "Fees &copy2024 and &para"),
    ("<b>Warning</b> it ends with <", "Warning it ends with <"),
    ("<b>Kept</b> <!-- a note --> after", "Kept <!-- a note --> after"),
]


@pytest.mark.parametrize("markup, words", LOSSLESS)
def test_no_character_of_the_post_is_lost_on_the_way_to_telegram(wired, markup, words):
    reply = messaging.send_telegram_message(to="ML community", text=markup)

    _, text, entities = wired.wire[-1]
    assert text == words, "characters of his post were dropped or changed"
    assert "MessageEntityBold" in _kinds(entities), "the formatting was lost"
    assert "without formatting" not in reply.lower()


def test_an_emoji_written_as_an_entity_does_not_shift_the_formatting(wired):
    """
    Telegram counts offsets in UTF-16, where this emoji is TWO units. Decoded
    by the parser from "&#128512;", it was counted as one, and every entity
    after it landed one short - here, the italic started on the space.
    """
    messaging.send_telegram_message(
        to="ML community", text="<b>&#128512; &#x1F600;</b> <i>x</i>"
    )
    _, text, entities = wired.wire[-1]
    assert text == "\U0001f600 \U0001f600 x"
    bold, italic = entities
    assert (bold.offset, bold.length) == (0, 5)
    assert (italic.offset, italic.length) == (6, 1)


def test_words_the_parser_would_still_drop_go_out_plain_and_say_so(wired):
    """
    The last line of defence: compare what the parser produced with the words
    that were written. Telethon replaces a mailto link's words with the
    address, so "write to the lab" never arrived.
    """
    markup = '<b>Contact</b> <a href="mailto:lab@uni.edu">write to the lab</a>'
    reply = messaging.send_telegram_message(to="ML community", text=markup)

    _, text, entities = wired.wire[-1]
    assert "write to the lab" in text
    assert entities == []
    assert reply.startswith("Sent to") and "without formatting" in reply.lower()


# ------------------------------- 5. text that only LOOKS like markup (finding 2)
# The detector used to decide for the whole message: one closing tag or entity
# anywhere, and everything tag-shaped was parsed - and eaten.
AMBIGUOUS = [
    ("send the literal </b> please", "</b>"),
    ("use <b> to bold, and &amp; for ampersand", "<b>"),
    ("<b>x</b> a<b and c>d", "<b and c>"),
]


@pytest.mark.parametrize("text, culprit", AMBIGUOUS)
def test_text_that_only_looks_like_markup_is_never_eaten(wired, text, culprit):
    """
    Either every tag is Telegram formatting, opened and closed in order, or
    nothing is guessed: nothing goes out, and the reply names the tag and says
    how to send it literally. Sending it as written would put <b> hieroglyphs
    in his channel whenever the brain slips; parsing it ate his words.
    """
    reply = messaging.send_telegram_message(to="ML community", text=text)

    assert wired.wire == [], f"a mangled or unformatted version went out: {wired.wire}"
    assert reply.startswith("Nothing sent"), reply
    assert culprit in reply, "the reply does not say which tag was the problem"
    assert "&lt;" in reply, "the reply does not say how to send it literally"


def test_html_telegram_cannot_format_is_refused_not_posted_as_hieroglyphs(wired):
    """
    </p> is not a Telegram tag, so the old detector called this plain text and
    posted it as written - the hieroglyphs the whole fix was for, by the one
    route it left open. A closing tag of ANY name is now a sign of markup.
    """
    reply = messaging.send_telegram_message(
        to="ML community", text="<p>Lab Opportunity</p>\n<p>Apply by Friday</p>"
    )
    assert wired.wire == []
    assert reply.startswith("Nothing sent") and "<p>" in reply


def test_a_literal_tag_written_as_entities_goes_out_exactly(wired):
    messaging.send_telegram_message(
        to="ML community", text="send the literal &lt;/b&gt; please"
    )
    assert wired.wire[-1][1] == "send the literal </b> please"


def test_a_draft_with_ambiguous_markup_is_not_saved_mangled(wired):
    reply = messaging.save_telegram_draft(to="ML community", text="<b>x</b> a<b and c>d")
    assert wired.requests == [], "the draft box got a guess at what he meant"
    assert reply.startswith("Nothing saved")


# ----------------------------------------------- 6. spoilers (finding 3)
@pytest.mark.parametrize("markup", [
    "<tg-spoiler>the butler did it</tg-spoiler>",
    '<span class="tg-spoiler">the butler did it</span>',
])
def test_a_spoiler_is_hidden_not_posted_in_the_clear(wired, markup):
    messaging.send_telegram_message(to="ML community", text=f"<b>Answer:</b> {markup}")

    _, text, entities = wired.wire[-1]
    assert text == "Answer: the butler did it"
    spoilers = [e for e in entities if type(e).__name__ == "MessageEntitySpoiler"]
    assert spoilers, "the spoiler went to his channel in the clear"
    hidden = spoilers[0]
    assert text[hidden.offset:hidden.offset + hidden.length] == "the butler did it"


def test_ins_and_strike_are_formatted_not_dropped(wired):
    messaging.send_telegram_message(
        to="ML community", text="<b>a</b> <ins>under</ins> <strike>gone</strike>"
    )
    kinds = _kinds(wired.wire[-1][2])
    assert {"MessageEntityUnderline", "MessageEntityStrike"} <= kinds


def test_every_tag_the_post_guide_promises_is_delivered(wired):
    """
    The guide is what the brain believes. A tag it lists that arrives as
    nothing - <tg-spoiler> did - is a promise the send path does not keep.
    """
    guide = (Path(__file__).resolve().parent.parent / "docs" /
             "community_post_format.md").read_text(encoding="utf-8")
    section = guide.split("## Telegram HTML", 1)[1].split("\n---", 1)[0]
    promise = next(p for p in section.split("\n\n") if p.lstrip().startswith("`<"))
    promised = re.findall(r"`(<[a-z][^`]*>)`", promise)
    assert "<tg-spoiler>" in promised and len(promised) >= 8

    for opening in promised:
        name = re.match(r"<([a-z-]+)", opening).group(1)
        opening = re.sub(r'href="[^"]*"', 'href="https://example.com"', opening)
        # The guide writes the premium emoji's id as the placeholder ID, and
        # the tag wraps one emoji, not a word. A real-looking id and an emoji
        # make it the post the guide describes.
        opening = re.sub(r'emoji-id="[^"]*"', 'emoji-id="5368324170671202286"', opening)
        body = "\U0001f680" if name == "tg-emoji" else "word"
        wired.wire.clear()
        messaging.send_telegram_message(to="ML community", text=f"{opening}{body}</{name}>")
        assert wired.wire, f"{opening} was refused"
        _, text, entities = wired.wire[-1]
        assert text == body and entities, f"{opening} arrived as no formatting"


def test_a_rejected_spoiler_is_never_retried_in_the_clear(monkeypatch):
    """
    The plain-text retry is right for bold. For a spoiler it publishes the
    very words the spoiler hid.
    """
    client = _wire_client(monkeypatch, FakeClient(reject_entities_once=True))
    reply = messaging.send_telegram_message(
        to="ML community",
        text="<b>Answer:</b> <tg-spoiler>the butler did it</tg-spoiler>",
    )
    assert client.wire == [], "the hidden text went out in the clear"
    assert reply.startswith("Nothing sent") and "spoiler" in reply.lower()


# ------------------------------------- 7. a file's caption (finding 4)
def test_a_rejected_caption_is_retried_plain_and_says_so(monkeypatch, tmp_path):
    client = _wire_client(monkeypatch, FakeClient(reject_entities_once=True))
    attachments = _a_poster(monkeypatch, tmp_path)

    reply = attachments.send_telegram_file(
        to="ML community", file="poster", caption="<b>Poster</b> for Q&amp;A"
    )
    assert len(client.wire) == 1, "a rejected request delivered nothing; it must retry once"
    kind, text, entities = client.wire[0]
    assert kind == "file" and text == "Poster for Q&A" and entities == []
    assert reply.startswith("Sent poster.pdf") and "without formatting" in reply.lower()


def test_a_timeout_is_never_retried_because_it_may_have_delivered(monkeypatch, tmp_path):
    client = _wire_client(monkeypatch, FakeClient(fail_with=TimeoutError()))
    attachments = _a_poster(monkeypatch, tmp_path)

    for send in (
        lambda: messaging.send_telegram_message(to="ML community", text=POST),
        lambda: attachments.send_telegram_file(
            to="ML community", file="poster", caption="<b>Poster</b>"),
    ):
        before = client.attempts
        try:
            send()
        except TimeoutError:
            pass
        assert client.attempts - before == 1, "a timeout was retried - a possible double post"


# ------------------------------- 8. what send_posts believes (finding 5)
def test_a_delivered_post_that_says_couldnt_is_counted_sent(wired):
    """
    The reply quotes the post. A post that says "couldn't" was counted FAILED
    after it had gone out, and a failed post is one he sends again.
    """
    from jarvis.tools import drafting

    post = "<b>We couldn't be prouder</b>: nothing sent us this far but you."
    result = drafting.send_posts(to="ML community", posts=[post])

    assert len(wired.wire) == 1
    assert result.startswith("1 of 1 sent"), result
    assert "FAILED" not in result


def test_a_post_whose_fate_is_unknown_is_not_reported_failed(monkeypatch):
    """
    A timeout may have delivered. FAILED tells him to send it again.
    """
    from jarvis.tools import drafting

    def send(to, text):
        if text == "b":
            raise TimeoutError()
        return f"Sent to {to}: {text!r}"

    monkeypatch.setattr(messaging, "send_telegram_message", send)
    result = drafting.send_posts(to="X", posts=["a", "b", "c"])

    assert "2 of 3 sent" in result
    assert "FAILED" not in result, result
    assert "NOT CONFIRMED (1)" in result and "before sending" in result.lower()


# --------------------------------- 9. nothing left to send (finding 6)
def test_a_post_with_no_words_is_refused_in_a_sentence(wired):
    reply = messaging.send_telegram_message(to="ML community", text="<b></b>")
    assert wired.wire == []
    assert reply.startswith("Nothing sent")


def test_an_empty_draft_does_not_clear_the_one_already_there(wired):
    """Saving an empty draft is how Telegram DELETES the draft in that chat."""
    reply = messaging.save_telegram_draft(to="ML community", text="<b> </b>")
    assert wired.requests == []
    assert reply.startswith("Nothing saved")
