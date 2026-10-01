"""
His premium emoji and his stickers: find them, and send a sticker.

    find_premium_emoji   GREEN   the <tg-emoji> tag for an emoji he has, by
                                 character or by name, preferring the ones he
                                 already uses in his channel
    list_sticker_packs   GREEN   which emoji sets and sticker packs he has, or
                                 what is inside one
    send_sticker         RED     a sticker as its own message - the same tier
                                 and the same pre-approved destinations as
                                 send_telegram_message. ONLY WHEN HE ASKS for
                                 one: nothing sends a sticker after a post on
                                 its own (his decision, 2026-10-01)

WHY THIS EXISTS. A premium emoji in a post is <tg-emoji emoji-id="ID">E</tg-emoji>
and the id is the whole problem. The messaging path already carried the tag
through (messaging._TELEGRAM_TAGS), but nothing could produce an id, so on
2026-08-29 - after he had asked six times on four days between 08-21 and
08-29 - the brain told him it had no access to premium emoji. data/audit.jsonl,
read 2026-10-01: 5,046 rows, six utterances asking for premium emoji or
stickers (08-21, 08-23, three on 08-26, 08-29), 17 Telegram send attempts,
not one with a tg-emoji tag in it.

WHERE THE IDS COME FROM. Telegram keeps them on his account:

    messages.getEmojiStickers      the custom emoji SETS he has installed
    messages.getAllStickers        his sticker packs
    messages.getStickerSet         what is inside one (documents, the emoji each
                                   stands in for, the keywords it is filed under)
    messages.getFavedStickers      his favourite stickers

each read from the installed Telethon 1.44 (telethon/tl/functions/messages.py),
not from memory of the API. And from his own posts: an entity of type
MessageEntityCustomEmoji carries the document id, so reading the channel he
posts in shows which emoji he already uses and where - the pattern a new post
should follow.

THE TAINT TRADE-OFF, and why find_premium_emoji prints no names
---------------------------------------------------------------
"Write the post, add the emoji, send it" is one turn. Any tool that fences its
output as untrusted content marks that turn (taint.mark), and a marked turn
cannot post to his channel at all: SafetyEngine.classify refuses it as
origin='content', which is the injection guard working as designed.

Pack titles, short names and keywords are written by whoever made the pack, so
they ARE untrusted text. So:

  * find_premium_emoji shows ids, emoji characters and counts only. Nothing a
    stranger wrote reaches the brain, which is what lets it skip the fence and
    leave the turn clean for the send that follows. "An emoji character" is
    held to a strict shape (_emoji_only): a real pictograph, an optional
    presentation selector and skin tone, joined by zero-width joiners, a flag,
    a keycap, or one of the three subdivision flags. Invisible Unicode tag
    characters, circled letters and the rest of what could spell words are
    refused, because a first version accepted them (see test_premium_emoji_review).
  * list_sticker_packs, whose overview has to show titles (he asked what he
    has), fences them like every other reader and marks the turn. Listing the
    stickers INSIDE one pack he named shows emoji characters and numbers only,
    so "show me that pack, then send number 4" still works.

It never invents an id. "None found" is an answer; a plausible number is a
broken emoji in his channel.

NOT VERIFIED ON HIS ACCOUNT. All of this was built and tested against fakes
made from Telethon's own types; his real account was never touched. Unchecked:
that getEmojiStickers returns his sets and in what order, that Telegram drops
custom emoji without an error for an account that is not Premium (the read-back
exists because of that belief), and that send_file of a Document arrives as a
sticker. A Premium account can also use custom emoji from sets it has not
installed; those cannot be found here, by character or by name, and the answer
says the emoji stays ordinary. Before relying on it: run one lookup, one send
and the read-back against his own Saved Messages, and time the lookup to replace
the [NOT MEASURED] constants below.
"""
from __future__ import annotations

import asyncio
import time
from collections import Counter
from typing import Any, NamedTuple

from . import messaging

# ------------------------------------------------------------------ limits
# Every constant below is a judgement, not a measurement: nothing here could be
# timed offline, and the real account was never touched (read-only was the
# brief, and a session file is not read). Labelled so, per the convention.

# [NOT MEASURED] Most emoji sets read in one lookup. One request per set, so a
# collection of hundreds would otherwise run past RUNTIME.run's 60 s wait. A
# reply that stopped at the cap says so; it never reads as "nothing found".
_MAX_SETS = 40

# [NOT MEASURED] Requests in flight at once. Telegram answers a getStickerSet
# in well under a second on a normal link; 6 keeps the whole lookup inside the
# 60 s wait without asking for a flood.
_PARALLEL = 6

# [NOT MEASURED] How long a complete emoji index, and each emoji set read for
# one, is reused. Premium emoji ids never change; the only staleness is a set he
# installed a minute ago. Ten minutes is one working session on a post.
_INDEX_TTL_S = 600.0

# [NOT MEASURED] Recent messages read to learn which emoji he uses. The log
# holds 16 send_telegram_message attempts (data/audit.jsonl, 5,046 rows, read
# 2026-10-01): 14 to Saved Messages (13 sent, 1 blocked), 1 to the channel and 1
# to a person he cancelled. That says how little he posts from here, not how
# often he posts, so 30 is a guess at roughly a month of his own posts.
_LEARN_LIMIT = 30

# [NOT MEASURED] Entries printed for one pack before "and N more".
_LIST_LIMIT = 60

# [NOT MEASURED] Matches printed for one NAME query ("rocket"). A character
# query gets one answer; a name can fit many.
_NAME_MATCHES = 5

_VS16, _VS15, _ZWJ = chr(0xFE0F), chr(0xFE0E), chr(0x200D)
_KEYCAP = chr(0x20E3)

# [NOT MEASURED] The most code points one "emoji" printed by these tools may
# have. The longest sequences I know are 10 (a kiss with two skin tones, joined
# with ZWJ) and 7 (a family, an England flag); 12 clears them. It is a belt:
# _emoji_only also demands a real shape, so it is not what keeps text out.
_MAX_EMOJI_CODEPOINTS = 12

# Unicode's Extended_Pictographic property (UTS #51, emoji-data.txt, Unicode
# 15.1), written out because Python's unicodedata does not carry it. Anything
# that is not in here is not a pictograph for these tools' purposes, so an
# emoji missing from it is merely left ordinary. What matters is what is NOT in
# it: no letters, no digits, no circled or squared letters (the A-Z of the
# enclosed alphanumerics), no regional indicators, nothing that is format
# characters. tests/test_premium_emoji_review.py checks that on every code
# point listed, against Python's own character categories.
_PICTOGRAPHIC: tuple[tuple[int, int], ...] = (
    (0x00A9, 0x00A9), (0x00AE, 0x00AE), (0x203C, 0x203C), (0x2049, 0x2049),
    (0x2122, 0x2122), (0x2139, 0x2139), (0x2194, 0x2199), (0x21A9, 0x21AA),
    (0x231A, 0x231B), (0x2328, 0x2328), (0x2388, 0x2388), (0x23CF, 0x23CF),
    (0x23E9, 0x23F3), (0x23F8, 0x23FA), (0x24C2, 0x24C2), (0x25AA, 0x25AB),
    (0x25B6, 0x25B6), (0x25C0, 0x25C0), (0x25FB, 0x25FE), (0x2600, 0x2605),
    (0x2607, 0x2612), (0x2614, 0x2685), (0x2690, 0x2705), (0x2708, 0x2712),
    (0x2714, 0x2714), (0x2716, 0x2716), (0x271D, 0x271D), (0x2721, 0x2721),
    (0x2728, 0x2728), (0x2733, 0x2734), (0x2744, 0x2744), (0x2747, 0x2747),
    (0x274C, 0x274C), (0x274E, 0x274E), (0x2753, 0x2755), (0x2757, 0x2757),
    (0x2763, 0x2767), (0x2795, 0x2797), (0x27A1, 0x27A1), (0x27B0, 0x27B0),
    (0x27BF, 0x27BF), (0x2934, 0x2935), (0x2B05, 0x2B07), (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50), (0x2B55, 0x2B55), (0x3030, 0x3030), (0x303D, 0x303D),
    (0x3297, 0x3297), (0x3299, 0x3299),
    (0x1F000, 0x1F0FF), (0x1F10D, 0x1F10F), (0x1F12F, 0x1F12F),
    (0x1F16C, 0x1F171), (0x1F17E, 0x1F17F), (0x1F18E, 0x1F18E),
    (0x1F191, 0x1F19A), (0x1F1AD, 0x1F1E5), (0x1F201, 0x1F20F),
    (0x1F21A, 0x1F21A), (0x1F22F, 0x1F22F), (0x1F232, 0x1F23A),
    (0x1F23C, 0x1F23F), (0x1F249, 0x1F3FA), (0x1F400, 0x1F53D),
    (0x1F546, 0x1F64F), (0x1F680, 0x1F6FF), (0x1F774, 0x1F77F),
    (0x1F7D5, 0x1F7FF), (0x1F80C, 0x1F80F), (0x1F848, 0x1F84F),
    (0x1F85A, 0x1F85F), (0x1F888, 0x1F88F), (0x1F8AE, 0x1F8FF),
    (0x1F90C, 0x1F93A), (0x1F93C, 0x1F945), (0x1F947, 0x1FAFF),
    (0x1FC00, 0x1FFFD),
)

# The only tag-character sequences that are emoji: the flags of England,
# Scotland and Wales, a black flag then the invisible spelling of gbeng, gbsct or
# gbwls and a cancel tag. Spelled out in full rather than described by shape,
# because any tag run a shape would allow is a way to hide ASCII text in an
# emoji (U+E0020..U+E007E are invisible copies of ASCII).
_SUBDIVISION_FLAGS = frozenset(
    chr(0x1F3F4) + "".join(chr(0xE0000 + ord(c)) for c in code) + chr(0xE007F)
    for code in ("gbeng", "gbsct", "gbwls")
)


# ------------------------------------------------------------ small helpers
def _bare(char: str) -> str:
    """An emoji without its text/emoji presentation selector, for comparing."""
    return (char or "").replace(_VS16, "").replace(_VS15, "").strip()


def _pictographic(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in _PICTOGRAPHIC)


def _one_pictograph(part: str) -> bool:
    """One pictograph, then at most one presentation selector, then at most one skin tone."""
    if not part or not _pictographic(part[0]):
        return False
    rest = part[1:]
    if rest[:1] in (_VS16, _VS15):
        rest = rest[1:]
    if rest[:1] and 0x1F3FB <= ord(rest[0]) <= 0x1F3FF:
        rest = rest[1:]
    return rest == ""


def _emoji_only(text: Any) -> bool:
    """
    Is `text` ONE emoji, and nothing a person could have hidden words in?

    This is what lets find_premium_emoji skip the untrusted fence. It prints
    "an emoji character" taken from places strangers control: the text under a
    custom-emoji entity in a message (Telegram lets one cover any run of text,
    and learn_from can name a group), and the `alt` of a sticker or emoji in a
    pack its author made. Anything printed has to pass this first, and what
    fails is left out, never trimmed or quoted.

    A SHAPE, not "nothing that looks like a word". The first version let
    through anything that was not a letter, so a rocket followed by ten
    invisible tag characters (ASCII text, unseen) and a run of circled letters
    joined with zero-width joiners both counted as one emoji, and the brain
    read them with no fence on a turn still marked as his own words. gmail's
    _readable strips every format character for the same reason. What is
    accepted now, and only this:

      * a flag: exactly two regional indicators;
      * a keycap: 0-9, # or *, an optional U+FE0F, then U+20E3;
      * England, Scotland or Wales: the three sequences in _SUBDIVISION_FLAGS;
      * pictographs (_PICTOGRAPHIC), each with at most a presentation selector
        and a skin tone, joined by zero-width joiners with nothing left over
        at either end.

    Every other format character, every other tag, letters and digits, circled
    and squared letters, and anything longer than _MAX_EMOJI_CODEPOINTS is
    refused. What a chain of up to six pictographs could carry is a handful of
    symbols, which is not a sentence.
    """
    if not isinstance(text, str) or not text or len(text) > _MAX_EMOJI_CODEPOINTS:
        return False
    if text in _SUBDIVISION_FLAGS:
        return True
    if len(text) == 2 and all(0x1F1E6 <= ord(ch) <= 0x1F1FF for ch in text):
        return True
    if text[0] in "0123456789#*" and text[1:] in (_KEYCAP, _VS16 + _KEYCAP):
        return True
    return all(_one_pictograph(part) for part in text.split(_ZWJ))


def _tag(doc_id: int, char: str) -> str:
    return f'<tg-emoji emoji-id="{doc_id}">{char}</tg-emoji>'


def _plain(text: str) -> str:
    """Lower-case words only: 'Tech_Memes!' and 'tech memes' are one name."""
    return " ".join("".join(c if c.isalnum() else " " for c in (text or "").lower()).split())


def _alt(doc, attribute_name: str):
    """The emoji a document stands in for, or None when it is not that kind."""
    for attribute in getattr(doc, "attributes", None) or []:
        if type(attribute).__name__ == attribute_name:
            return getattr(attribute, "alt", "") or ""
    return None


def _clusters(run: str) -> list[str]:
    """
    Split a run of emoji into one entry per emoji, keeping together what
    belongs together: a variation selector, a skin tone, a keycap, a
    zero-width-joiner sequence, a pair of regional indicators (a flag), and
    the tag characters that spell a subdivision flag (England, Scotland).
    """
    out: list[str] = []
    join_next = False
    for ch in run:
        code = ord(ch)
        glue = (ch in (_VS16, _VS15, _ZWJ) or 0x1F3FB <= code <= 0x1F3FF
                or code == 0x20E3 or 0xE0020 <= code <= 0xE007F)
        flag_second = (0x1F1E6 <= code <= 0x1F1FF and out and len(out[-1]) == 1
                       and 0x1F1E6 <= ord(out[-1]) <= 0x1F1FF)
        if out and (glue or join_next or flag_second):
            out[-1] += ch
        else:
            out.append(ch)
        join_next = ch == _ZWJ
    return out


class _Want(NamedTuple):
    text: str          # what he asked for: an emoji, or a word
    is_emoji: bool


def _parse_wants(query: str) -> list[_Want]:
    """'a b', 'a,b' and 'ab' (a run of emoji with no spaces) all work."""
    wants: list[_Want] = []
    for token in (query or "").replace(",", " ").replace(";", " ").split():
        looks_like_emoji = (any(ord(c) > 0x2000 for c in token)
                            and not any(c.isascii() and c.isalnum() for c in token))
        if looks_like_emoji:
            wants += [_Want(c, True) for c in _clusters(token)]
        else:
            wants.append(_Want(token.lower().strip(".:!?\"'"), False))
    seen, unique = set(), []
    for want in wants:
        key = (_bare(want.text), want.is_emoji)
        if want.text and key not in seen:
            seen.add(key)
            unique.append(want)
    return unique


# ------------------------------------------------------------- reading sets
async def _installed(client, request) -> list:
    result = await client(request)
    return list(getattr(result, "sets", None) or [])


async def _emoji_infos(client) -> list:
    from telethon.tl.functions.messages import GetEmojiStickersRequest

    return [i for i in await _installed(client, GetEmojiStickersRequest(hash=0))
            if not getattr(i, "masks", None)]


async def _sticker_infos(client) -> list:
    from telethon.tl.functions.messages import GetAllStickersRequest

    # getAllStickers is the ordinary packs; the flags are checked anyway, so a
    # mask or an emoji set that turned up here is not offered as a sticker pack.
    return [i for i in await _installed(client, GetAllStickersRequest(hash=0))
            if not getattr(i, "masks", None) and not getattr(i, "emojis", None)]


async def _fetch(client, info):
    """messages.getStickerSet for one set. hash=0: always the whole set."""
    from telethon.tl.functions.messages import GetStickerSetRequest
    from telethon.tl.types import InputStickerSetID

    return await client(GetStickerSetRequest(
        stickerset=InputStickerSetID(id=info.id, access_hash=info.access_hash),
        hash=0,
    ))


async def _fetch_many(client, infos) -> list:
    """[(info, the set or None when it could not be read)], in order."""
    gate = asyncio.Semaphore(_PARALLEL)

    async def one(info):
        async with gate:
            try:
                return info, await _fetch(client, info)
            except Exception:  # noqa: BLE001 - one dead pack must not lose the rest
                return info, None

    return list(await asyncio.gather(*(one(i) for i in infos)))


# ------------------------------------------------------- the emoji index
class _Entry(NamedTuple):
    doc_id: int
    char: str                # the emoji it stands in for
    order: int               # earlier sets, and earlier places in a set, first
    keywords: frozenset      # lower-case words Telegram files it under


class _Index(NamedTuple):
    entries: list
    total: int               # emoji sets he has
    looked_at: int           # how many of them were read (capped)
    failed: int              # how many of those could not be read
    skipped: int = 0         # custom emoji left out: text, not an emoji, is stored for them


class _OneSet(NamedTuple):
    emoji: list              # [(document id, emoji, keywords)] in the set's order
    skipped: int             # documents whose stored emoji is not an emoji


_INDEX: "tuple[float, _Index] | None" = None
# One entry per emoji set that was read successfully: id -> (read at, _OneSet).
# A set that could not be read is NOT kept, so a dead one is tried again and the
# live ones are not read twice.
_SETS: "dict[int, tuple[float, _OneSet]]" = {}


def _forget() -> None:
    """Drop what is cached. Tests use it; so does a set he just installed."""
    global _INDEX
    _INDEX = None
    _SETS.clear()


def _read_one_set(full) -> _OneSet:
    keywords: dict[int, set] = {}
    for found in getattr(full, "keywords", None) or []:
        keywords.setdefault(found.document_id, set()).update(
            str(word).lower() for word in found.keyword or [])
    emoji, skipped = [], 0
    for doc in getattr(full, "documents", None) or []:
        char = _alt(doc, "DocumentAttributeCustomEmoji")
        if char is None:
            continue                       # not a custom emoji at all
        if not _emoji_only(char):
            # The pack's author chose this text. Not an emoji: not offered, and
            # counted so that an answer can say it left some out.
            skipped += 1
            continue
        emoji.append((doc.id, char, frozenset(keywords.get(doc.id, ()))))
    return _OneSet(emoji, skipped)


async def _emoji_index(client) -> _Index:
    """
    Every custom emoji in his sets, in the order he has them.

    A complete read is reused whole for _INDEX_TTL_S with no requests at all.
    An incomplete one (a set that cannot be read) is not, so the next lookup
    gives that set another chance - but the sets that DID read are kept one by
    one, so one persistently dead set costs one extra request per lookup and not
    a re-read of all forty.
    """
    global _INDEX
    now = time.monotonic()
    if _INDEX is not None and now - _INDEX[0] < _INDEX_TTL_S:
        return _INDEX[1]

    infos = await _emoji_infos(client)
    chosen = infos[:_MAX_SETS]
    read: dict[int, _OneSet] = {}
    todo = []
    for info in chosen:
        kept = _SETS.get(info.id)
        if kept is not None and now - kept[0] < _INDEX_TTL_S:
            read[info.id] = kept[1]
        else:
            todo.append(info)
    failed = 0
    for info, full in await _fetch_many(client, todo):
        if full is None:
            failed += 1
            continue
        read[info.id] = _read_one_set(full)
        _SETS[info.id] = (now, read[info.id])
    still_his = {info.id for info in chosen}
    for gone in [set_id for set_id in _SETS if set_id not in still_his]:
        del _SETS[gone]                    # a set he removed

    entries: list[_Entry] = []
    skipped = 0
    for info in chosen:                    # his order, not the order they arrived in
        one = read.get(info.id)
        if one is None:
            continue
        skipped += one.skipped
        for doc_id, char, words in one.emoji:
            entries.append(_Entry(doc_id, char, len(entries), words))
    index = _Index(entries, len(infos), len(chosen), failed, skipped)
    if not failed:
        _INDEX = (now, index)
    return index


def _by_character(index: _Index, char: str) -> list[_Entry]:
    """
    The premium emoji that ARE `char`, and no other.

    Telegram also files a premium emoji under other emoji it resembles
    (messages.StickerPack.emoticon), and that is how a flying saucer got offered
    for a rocket: the tag put a rocket inside it, premium viewers saw a saucer,
    and everyone else saw a rocket. A different picture in his channel is worse
    than an ordinary emoji, so only a match on the emoji itself counts.
    """
    key = _bare(char)
    return [e for e in index.entries if _bare(e.char) == key]


def _by_name(index: _Index, word: str) -> list[_Entry]:
    """Whole-word match, or a substring for words of three letters or more."""
    word = word.lower()
    return [
        e for e in index.entries
        if any(k == word or (len(word) >= 3 and word in k) for k in e.keywords)
    ]


# ------------------------------------------------- learning from his posts
class _Use:
    def __init__(self, char: str) -> None:
        self.char = char
        self.posts = 0
        self.slots: Counter = Counter()


class _Learned(NamedTuple):
    chat: str
    total: int               # posts with words in them that were read
    uses: dict               # document id -> _Use
    error: str = ""          # why his posts could not be read, if they could not


def _slot(text: str, offset: int) -> str:
    """
    Where in a post an emoji sits, as his format names it. The offset is in
    UTF-16 units, which is how Telegram counts, so the prefix is taken in
    those and not in Python characters.
    """
    prefix = text.encode("utf-16-le")[: offset * 2].decode("utf-16-le", errors="ignore")
    lines = text.split("\n")
    line_no = prefix.count("\n")
    before = prefix.rsplit("\n", 1)[-1]
    filled = [i for i, line in enumerate(lines) if line.strip()]
    if filled and line_no == filled[0]:
        return "the title line"
    if before.rstrip().endswith(":"):
        return "a section heading"
    if filled and line_no in filled[-2:]:
        return "the sign-off"
    return "the body"


async def _learn(client, chat: str) -> "_Learned | None":
    """
    Read-only: which premium emoji his recent posts in `chat` use, and where.

    None when there is no such chat. A chat that exists but cannot be read
    comes back with `error` set, so the lookup can carry on from his emoji
    sets and say what it could not do.
    """
    from telethon import utils

    # The chat is called what he (or the brain, from his words) called it, not
    # by the title Telegram holds: a group's title is written by its admins,
    # and this tool prints nothing a stranger wrote - see the module docstring.
    name = chat
    try:
        entity = await messaging._resolve(client, chat)
        if entity is None:
            return None
        messages = await client.get_messages(entity, limit=_LEARN_LIMIT) or []
    except Exception as exc:  # noqa: BLE001 - spoken, not raised
        return _Learned(name, 0, {}, type(exc).__name__)
    uses: dict[int, _Use] = {}
    total = 0
    for message in messages:
        text = getattr(message, "raw_text", None) or getattr(message, "message", "") or ""
        if not text.strip():
            continue
        total += 1
        entities = [e for e in getattr(message, "entities", None) or []
                    if messaging._is_custom_emoji(e)]
        counted: set[int] = set()
        for entity_, char in zip(entities, utils.get_inner_text(text, entities)):
            if not _emoji_only(char):
                # An entity over words (a stranger's message in a group, say)
                # is not an emoji he uses, and its text is never printed.
                continue
            use = uses.setdefault(entity_.document_id, _Use(char))
            if entity_.document_id not in counted:
                use.posts += 1
                counted.add(entity_.document_id)
            use.slots[_slot(text, entity_.offset)] += 1
    return _Learned(name, total, uses)


def _read(work, what: str, advice: str, lead: str = ""):
    """
    messaging.RUNTIME.run for work that only READS, with a sentence for a slow
    or broken Telegram: the work's own result, or a str starting with `lead`.

    A bare RUNTIME.run lets TimeoutError out, and the tool wrapper turns that
    into "find_premium_emoji failed: TimeoutError" - which the brain has
    already shown it will turn into "I can't use premium emoji" (2026-08-29).
    Nothing is sent by this work, so unlike messaging._run_send there is no
    "Not confirmed" to worry about: when the wait runs out the coroutine goes on
    reading on the Telegram loop, and it cannot send. send_sticker relies on
    that for its search. Not being signed in still raises: it carries the fix.
    """
    try:
        return messaging.RUNTIME.run(work)
    except messaging.TelegramNotConnected:
        raise
    except TimeoutError:
        return f"{lead}Telegram didn't answer in time, so I couldn't {what}. {advice}"
    except Exception as exc:  # noqa: BLE001 - spoken, not raised
        return f"{lead}I couldn't reach Telegram to {what} ({type(exc).__name__}). {advice}"


# --------------------------------------------------------- find_premium_emoji
_FOOTER = (
    "Put each tag exactly where the ordinary emoji was, and keep the emoji "
    "between the tags: it is what shows without Premium. These ids are real - "
    "they come from his account - so never write one from memory."
)


def find_premium_emoji(query: str = "", learn_from: str = "") -> str:
    """
    The <tg-emoji> tag for each emoji in `query`, from his own account. GREEN.

    `query` is characters ("a b c", or a run of them) and/or words. With
    `learn_from` - the chat he posts in - the emoji he already uses there are
    offered first, and with no query it describes the pattern he follows.
    """
    messaging._enabled()
    wants = _parse_wants(query)
    chat = (learn_from or "").strip()
    if not wants and not chat:
        return (
            "Which emoji? Give me the characters (the ones in the post format "
            "guide) or a name like rocket, or give learn_from the channel to "
            "see which ones he already uses there."
        )

    async def work(client):
        notes: list[str] = []
        learned = None
        if chat:
            learned = await _learn(client, chat)
            if learned is None:
                notes.append(
                    f"I couldn't find a chat called {chat!r} to learn from, so "
                    "these come from his emoji sets only."
                )
            elif learned.error:
                notes.append(
                    f"I couldn't read his posts in {chat!r} ({learned.error}), "
                    "so these come from his emoji sets only."
                )
                learned = None

        if not wants:
            return _describe_pattern(learned, notes)

        try:
            index = await _emoji_index(client)
        except Exception as exc:  # noqa: BLE001 - spoken, not raised
            return (
                f"I couldn't read his emoji sets from Telegram ({type(exc).__name__}). "
                "Leave the emoji ordinary for now and tell him so."
            )
        lines, missed = [], False
        for want in wants:
            answer, found = _answer(want, index, learned)
            lines += answer
            missed = missed or not found
        header = (
            "Premium emoji from his own account"
            + (", the ones he already posts first." if learned and learned.uses else ".")
        )
        if learned is not None and not learned.uses:
            notes.append(
                f"None of his last {learned.total} posts in {learned.chat} use "
                "premium emoji yet, so these come from his emoji sets."
                if learned.total else
                f"There are no posts with words in {learned.chat} to learn from, "
                "so these come from his emoji sets."
            )
        if index.total == 0:
            notes.append("He has no custom emoji sets installed, so there is nothing to look in.")
        if index.failed:
            notes.append(
                f"{index.failed} of his {index.looked_at} emoji sets couldn't be "
                "read, so an emoji from one of those would not show up here."
            )
        if index.looked_at < index.total:
            notes.append(
                f"Only {index.looked_at} of {index.total} emoji sets were "
                "searched (the limit), so a missing one may be in the rest."
            )
        if index.skipped and missed:
            notes.append(_skipped_note(index.skipped))
        return "\n".join([header, *lines, *notes, _FOOTER])

    return _read(work, "look his premium emoji up",
                 "Leave the emoji ordinary for now and tell him so.")


def _skipped_note(count: int) -> str:
    """
    Custom emoji that cannot be offered, said without repeating what is stored.

    A custom emoji whose stored fallback is a word, a digit or anything that is
    not one emoji is not offered (see _emoji_only), and an answer that leaves
    some out has to say so, or "no premium emoji with that name" reads as "he
    has none". Telegram asks pack authors for an emoji here, so this is
    expected to be rare; it has not been seen on his account.
    """
    many = f"{count} custom emoji" if count != 1 else "1 custom emoji"
    return (
        f"{many} in his sets are stored with text or odd characters where the "
        "emoji should be, so I can't offer those."
    )


def _inside(asked: str, stored: str) -> str:
    """
    The character that goes BETWEEN the tags: the one he asked for, as he wrote
    it, when it is a valid emoji, else the pack's own (already validated).

    The pack stores its own spelling, and a pen written with the emoji selector
    (U+270F U+FE0F) came back inside a tag as the pen without it - a different
    character from the one the tag replaces, and the one non-Premium viewers
    see. They are equal once the selectors are stripped (_by_character), so
    nothing is swapped for something else, only the spelling is kept.
    """
    return asked if _emoji_only(asked) else stored


def _answer(want: _Want, index: _Index, learned: "_Learned | None") -> "tuple[list[str], bool]":
    """(the line or lines that answer one thing he asked for, whether a tag was found)."""
    if want.is_emoji:
        key = _bare(want.text)
        if learned is not None:
            mine = sorted(
                ((doc_id, use) for doc_id, use in learned.uses.items()
                 if _bare(use.char) == key),
                key=lambda pair: -pair[1].posts,
            )
            if mine:
                doc_id, use = mine[0]
                return [
                    f"{want.text}  ->  {_tag(doc_id, _inside(want.text, use.char))}  (the one "
                    f"in his posts: {use.posts} of his last {learned.total})"
                ], True
        found = _by_character(index, want.text)
        if found:
            first = found[0]
            return [f"{want.text}  ->  {_tag(first.doc_id, _inside(want.text, first.char))}  "
                    "(from his emoji sets)"], True
        return [
            f"{want.text}  ->  no premium version in his emoji sets - leave the "
            f"ordinary {want.text}"
        ], False
    found = _by_name(index, want.text)[:_NAME_MATCHES]
    if not found:
        return [f"{want.text}  ->  no premium emoji with that name in his emoji sets"], False
    return [f"{want.text}  ->  {_tag(e.doc_id, e.char)}" for e in found], True


def _describe_pattern(learned: "_Learned | None", notes: list[str]) -> str:
    if learned is None:
        return " ".join(notes) or "I couldn't read that chat."
    if not learned.uses:
        seen = (f"None of his last {learned.total} posts in {learned.chat} use "
                "premium emoji yet" if learned.total else
                f"There are no posts with words in {learned.chat} to learn from")
        return (
            f"{seen}, so there is no pattern to follow. Ask for the "
            "emoji by character and I'll look in his emoji sets."
        )
    lines = [
        f"His last {learned.total} posts in {learned.chat} use these premium "
        "emoji. Use the same ones in the same places:"
    ]
    for doc_id, use in sorted(learned.uses.items(), key=lambda p: -p[1].posts):
        where = use.slots.most_common(1)[0][0]
        lines.append(
            f"{_tag(doc_id, use.char)}  in {use.posts} of {learned.total} posts, "
            f"usually {where}"
        )
    return "\n".join([*lines, *notes, _FOOTER])


# --------------------------------------------------------- list_sticker_packs
def _kind(raw: str) -> "str | None":
    word = _plain(raw)
    if word in ("", "all", "both", "everything"):
        return "all"
    if word in ("emoji", "emojis", "custom emoji", "premium emoji", "emoji sets"):
        return "emoji"
    if word in ("sticker", "stickers", "packs", "sticker packs"):
        return "stickers"
    return None


def _find_pack(infos: list, wanted: str):
    """
    (info, "") when `wanted` names exactly one of `infos`, else (None, why).

    Exact first - the short name, or the title, loosely spelled - then ONE
    partial match, the rule messaging._resolve uses for chats: a wrong sticker
    in his channel is a post, so two fits is a refusal, not a guess. The
    candidates are not named in the reason: their titles are text strangers
    wrote.
    """
    key = _plain(wanted)
    if not key:
        return None, "no pack was named."
    exact = [i for i in infos
             if _plain(getattr(i, "short_name", "")) == key
             or _plain(getattr(i, "title", "")) == key]
    if len(exact) == 1:
        return exact[0], ""
    partial = exact or [i for i in infos
                        if key in _plain(getattr(i, "title", ""))
                        or key in _plain(getattr(i, "short_name", ""))]
    if len(partial) == 1:
        return partial[0], ""
    if partial:
        return None, (
            f"more than one of his packs fits {wanted!r} ({len(partial)} of "
            "them), so I won't guess. Name it more exactly."
        )
    return None, f"I couldn't find a pack called {wanted!r} among his."


def _sticker_docs_of(documents) -> list[tuple[Any, str]]:
    """The ordinary stickers among `documents`, in order. Never custom emoji."""
    out = []
    for doc in documents or []:
        alt = _alt(doc, "DocumentAttributeSticker")
        if alt is not None:
            # Still numbered when its emoji is not an emoji, so the numbers
            # match what the app shows; only the text is withheld.
            out.append((doc, alt if _emoji_only(alt) else ""))
    return out


def _sticker_docs(full) -> list[tuple[Any, str]]:
    """The ordinary stickers in a set, numbered by position."""
    return _sticker_docs_of(getattr(full, "documents", None))


def _more(shown: int, total: int) -> str:
    return f" ...and {total - shown} more." if total > shown else ""


def list_sticker_packs(kind: str = "all", pack: str = "") -> str:
    """
    His custom emoji sets and sticker packs, or what is inside one. GREEN.

    The overview shows pack titles, which strangers write, so it is fenced as
    untrusted and marks the turn. One named pack lists only emoji and numbers.
    """
    messaging._enabled()
    which = _kind(kind)
    if which is None:
        return "kind has to be emoji, stickers or all."
    named = (pack or "").strip()

    async def work(client):
        try:
            emoji_infos = await _emoji_infos(client) if which in ("all", "emoji") else []
            sticker_infos = await _sticker_infos(client) if which in ("all", "stickers") else []
        except Exception as exc:  # noqa: BLE001 - spoken, not raised
            return f"I couldn't read his packs from Telegram ({type(exc).__name__})."

        if named:
            return await _describe_pack(client, named, emoji_infos, sticker_infos)
        if not emoji_infos and not sticker_infos:
            return "He has no emoji sets or sticker packs on this account."

        lines = []
        if emoji_infos:
            lines.append(f"Custom emoji sets ({len(emoji_infos)}):")
            lines += [f"- {i.title} ({i.count} emoji)" for i in emoji_infos[:_LIST_LIMIT]]
        if sticker_infos:
            lines.append(f"Sticker packs ({len(sticker_infos)}):")
            lines += [f"- {i.title} ({i.count} stickers)" for i in sticker_infos[:_LIST_LIMIT]]
        # The titles are strangers' words: fenced, and the turn is marked.
        return messaging._fence(
            "\n".join(lines), "his Telegram sticker packs and emoji sets", limit=8000
        )

    return _read(work, "read his sticker packs", "Try again in a moment.")


async def _describe_pack(client, named: str, emoji_infos: list, sticker_infos: list) -> str:
    """One pack: its emoji and numbers, and nothing its author wrote."""
    problems = []
    for infos, label in ((sticker_infos, "stickers"), (emoji_infos, "emoji")):
        if not infos:
            continue
        info, why = _find_pack(infos, named)
        if info is None:
            problems.append(why)
            if why.startswith("more than one"):
                return why
            continue
        try:
            full = await _fetch(client, info)
        except Exception as exc:  # noqa: BLE001 - spoken, not raised
            return f"I couldn't read that pack ({type(exc).__name__})."
        if label == "stickers":
            docs = _sticker_docs(full)
            shown = "   ".join(f"{n} {alt or '(none)'}" for n, (_d, alt) in
                               enumerate(docs[:_LIST_LIMIT], start=1))
            return (
                f"That pack has {len(docs)} stickers, numbered:\n{shown}"
                f"{_more(_LIST_LIMIT, len(docs))}\n"
                "Send one with send_sticker: the same pack name, and either its "
                "number or its emoji."
            )
        one = _read_one_set(full)
        entries = [f"{char} id {doc_id}" for doc_id, char, _words in one.emoji]
        return (
            f"That emoji set has {len(entries)} custom emoji:\n"
            + "\n".join(entries[:_LIST_LIMIT]) + _more(_LIST_LIMIT, len(entries))
            + (f"\n{_skipped_note(one.skipped)}" if one.skipped else "")
            + "\nUse find_premium_emoji to get the tag for one."
        )
    return problems[0] if problems else f"I couldn't find a pack called {named!r} among his."


# ---------------------------------------------------------------- send_sticker
def _matching(docs: list, holder, want: str) -> list:
    """The (doc, alt) pairs for one emoji: by its own emoji, or filed under it."""
    related = {
        doc_id for pack in getattr(holder, "packs", None) or []
        if _bare(pack.emoticon) == want for doc_id in pack.documents or []
    }
    return [(d, a) for d, a in docs if _bare(a) == want or d.id in related]


def _which_pack(raw_emoji: str, fits: list, more: bool) -> str:
    """
    The question that stands in for a pick: which of the packs that have a
    sticker for this emoji does he mean.

    The titles are strangers' words, so they come back inside the fence and the
    turn is marked (messaging._fence), exactly like list_sticker_packs'
    overview. That is deliberate and it is the point: a marked turn cannot send
    a sticker (SafetyEngine refuses send_sticker under origin 'content'), so the
    brain cannot answer its own question - the pack is named in a turn of his
    own. The sentence outside the fence names no pack.
    """
    lines = [f"- {i.title} ({i.count} stickers)" for i in fits]
    fenced = messaging._fence(
        "\n".join(lines), f"his sticker packs with a sticker for {raw_emoji}", limit=2000)
    return (
        f"none of his favourites has a sticker for {raw_emoji}, and I won't choose a "
        "pack for him. Ask him which pack to take it from, and ask him to say it "
        "starting with \"Jalen,\" (or type it): a short spoken answer without the "
        "name is not taken as his own instruction after packs were read, so the "
        "send would be refused. Then send again with that pack named. These packs "
        f"have one:\n{fenced}"
        + ("\nThere may be more: I stopped at the first packs that had one." if more else "")
    )


async def _pick_sticker(client, named: str, raw_emoji: str, number: int):
    """
    ((document, its emoji, an extra sentence), "") or (None, why nothing).

    With a pack: that pack, and `number` (as list_sticker_packs numbers it) or
    the sticker for the emoji. Without one: his favourites, which are his own
    choice, so the first match there is not a guess. If none of them has the
    emoji nothing is picked: the reason is a question naming the packs that do
    (_which_pack). Two matches inside ONE pack are disclosed.
    """
    want = _bare(raw_emoji)
    if named:
        info, why = _find_pack(await _sticker_infos(client), named)
        if info is None:
            return None, why
        try:
            full = await _fetch(client, info)
        except Exception as exc:  # noqa: BLE001
            return None, f"I couldn't read that pack ({type(exc).__name__})."
        docs = _sticker_docs(full)
        if number:
            if not 1 <= number <= len(docs):
                return None, f"that pack has {len(docs)} stickers, so there is no number {number}."
            doc, alt = docs[number - 1]
            return (doc, alt, ""), ""
        if not want:
            if len(docs) == 1:
                return (docs[0][0], docs[0][1], ""), ""
            return None, (
                f"that pack has {len(docs)} stickers. Say which emoji it should "
                "match, or a number from list_sticker_packs."
            )
        found = _matching(docs, full, want)
        if not found:
            return None, f"that pack has no sticker for {raw_emoji}."
        extra = ""
        if len(found) > 1:
            extra = (f" There were {len(found)} stickers for {raw_emoji}; I sent "
                     "the first. Give a number to pick another.")
        return (found[0][0], found[0][1], extra), ""

    from telethon.tl.functions.messages import GetFavedStickersRequest

    unread: list[str] = []                 # what could not be read, for the "none" sentence
    try:
        faved = await client(GetFavedStickersRequest(hash=0))
    except Exception:  # noqa: BLE001 - his packs are the fallback
        faved = None
        unread.append("his favourites")
    if faved is not None:
        docs = _sticker_docs_of(getattr(faved, "stickers", None))
        found = _matching(docs, faved, want)
        if found:
            return (found[0][0], found[0][1], " It's one of his favourites."), ""

    every = await _sticker_infos(client)
    infos = every[:_MAX_SETS]
    failed = 0
    fits: list = []                        # the packs that have a sticker for it
    for start in range(0, len(infos), _PARALLEL):
        batch = infos[start:start + _PARALLEL]
        for info, full in await _fetch_many(client, batch):
            if full is None:
                failed += 1
                continue
            if _matching(_sticker_docs(full), full, want):
                fits.append(info)
        if fits:
            # NOTHING IS PICKED FOR HIM. The first version took the first match
            # in install order, sent it to his channel, and only then said
            # "none of his favourites matched, so this is the first one" - a
            # sticker from a pack called "Adult Memes" went out because "Tech
            # Memes" had no rocket. A sticker goes out only when he asks, and
            # which pack it comes from is part of what he asked, so with no
            # pack named and no favourite it is a question. Stopped after the
            # first batch that has any: more may follow, and the reply says so.
            return None, _which_pack(raw_emoji, fits, more=start + len(batch) < len(every))
    if failed:
        unread.append(f"{failed} of his {len(infos)} sticker packs")
    if not unread and len(every) <= len(infos):
        return None, (
            f"none of his favourites or his {len(infos)} sticker packs has a "
            f"sticker for {raw_emoji}."
        )
    # Not "has no sticker": part of it was never looked at, and saying so
    # would be a claim about stickers nobody read.
    reply = f"none of the stickers I could read is one for {raw_emoji}."
    if unread:
        reply += f" {' and '.join(unread)} couldn't be read, so one of those may have it."
    if len(every) > len(infos):
        reply += f" I only searched the first {len(infos)} of his {len(every)} packs."
    return None, reply


def send_sticker(to: str, emoji: str = "", pack: str = "", number: int = 0) -> str:
    """
    Send one sticker, as its own message, as him. RED unless the destination is
    pre-approved - the same rule, the same destinations and the same refusal
    under taint as send_telegram_message (SafetyEngine._DESTINATION_ARG).

    Only when he asks for one. Nothing here decides to send a sticker; the
    brain is told never to follow a post with one on its own (agent.py).

    TWO STEPS, because the first can be slow and only the second can send. Finding
    the sticker reads the chat, his favourites and up to _MAX_SETS packs. If
    Telegram does not answer in time while that runs, nothing has gone out and
    nothing will - the reply says so, and sending again is safe. Once the send
    itself is handed to Telegram a timeout proves nothing, which is the
    "Not confirmed" every send answers (messaging._run_send). Done as one piece
    of work, a slow search was reported as "may already be there" - and in that
    shape it was true, because RUNTIME.run stops waiting without stopping the
    work, which would then have gone on to send. The split is what makes the
    shorter answer true.
    """
    messaging._enabled()
    try:
        number = int(number or 0)
    except (TypeError, ValueError):
        number = 0
    named = (pack or "").strip()
    if not named and not _bare(emoji):
        return ("Nothing sent - tell me which emoji the sticker should match, or "
                "which pack to take it from.")
    if number and not named:
        return "Nothing sent - a number only means something inside a pack. Name the pack too."
    wanted = _bare(emoji)
    if wanted and not number and not _emoji_only(wanted):
        # A WORD FOR AN EMOJI. send_sticker(emoji="rocket") searched every pack
        # for a sticker filed under the letters r-o-c-k-e-t and answered "none of
        # his favourites or his 12 sticker packs has a sticker for rocket",
        # which reads as "he has no rocket sticker". Nothing is read for it.
        if any(ch.isalnum() for ch in wanted):
            return ("Nothing sent - the emoji has to be the character itself, not a "
                    "word like 'rocket'. Give the emoji character, or name a pack and a number.")
        return ("Nothing sent - a sticker matches one emoji. Give one emoji, or name a "
                "pack and a number.")

    async def look(client):
        # Reads only. The chat first: a wrong name must cost no pack reads.
        entity = await messaging._resolve(client, to)
        if entity is None:
            return f"I couldn't find a Telegram chat called {to!r} - nothing sent."
        picked, problem = await _pick_sticker(client, named, emoji, number)
        if picked is None:
            return f"Nothing sent - {problem}"
        return entity, picked

    found = _read(look, "look for the sticker",
                  "Nothing went out, so it is safe to try again.", lead="Nothing sent - ")
    if isinstance(found, str):
        return found
    entity, (doc, alt, extra) = found

    async def send(client):
        attempted.set()
        try:
            await client.send_file(entity, doc)
        except Exception as exc:
            # A refusal (a 400) proves nothing was delivered. Anything else may
            # have been: messaging._run_send answers "Not confirmed".
            if messaging._rejected(exc):
                return f"Nothing sent - Telegram refused it ({messaging._error_code(exc)})."
            raise
        name = await messaging._chat_name(client, entity)
        return f"Sent a {alt + ' ' if alt else ''}sticker to {name}.{extra}"

    attempted = messaging._Attempt()
    return messaging._run_send(send, to, attempted)


REGISTRY: dict[str, Any] = {
    "find_premium_emoji": find_premium_emoji,
    "list_sticker_packs": list_sticker_packs,
    "send_sticker": send_sticker,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
