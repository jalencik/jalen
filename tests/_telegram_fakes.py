"""
A fake Telegram account for the premium-emoji and sticker tests.

No network, no session, no browser. It is built from Telethon's OWN types
(Document, StickerSet, messages.StickerSet, MessageEntityCustomEmoji), so a
test that builds a set the wrong way fails here, not on his phone. And it
answers the real request classes by isinstance, so a tool that asks for the
wrong thing - say, a sticker set with no hash - fails the way Telethon would.

The one behaviour worth knowing about: `keeps_premium=False` imitates what
Telegram does for an account that is not Premium. It does NOT refuse a
message with custom-emoji entities; it accepts it and quietly drops them, so
the post arrives with ordinary emoji and the send "succeeds". That silence is
the reason a send has to be READ BACK.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timezone

from telethon import types
from telethon.tl import functions
from telethon.tl.types import messages as tmsg


# ------------------------------------------------------------------ builders
def emoji_doc(doc_id: int, alt: str, set_id: int = 100) -> types.Document:
    """A custom (premium) emoji, as Telegram stores it."""
    return types.Document(
        id=doc_id, access_hash=doc_id + 1, file_reference=b"ref",
        date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        mime_type="application/x-tgsticker", size=1, dc_id=2,
        attributes=[types.DocumentAttributeCustomEmoji(
            alt=alt, stickerset=types.InputStickerSetID(set_id, set_id * 7),
        )],
    )


def sticker_doc(doc_id: int, alt: str, set_id: int = 200) -> types.Document:
    """An ordinary sticker."""
    return types.Document(
        id=doc_id, access_hash=doc_id + 1, file_reference=b"ref",
        date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        mime_type="image/webp", size=1, dc_id=2,
        attributes=[types.DocumentAttributeSticker(
            alt=alt, stickerset=types.InputStickerSetID(set_id, set_id * 7),
        )],
    )


def set_info(set_id: int, title: str, short_name: str, count: int,
             emojis: bool = False) -> types.StickerSet:
    return types.StickerSet(
        id=set_id, access_hash=set_id * 7, title=title, short_name=short_name,
        count=count, hash=1, emojis=True if emojis else None,
    )


def full_set(info, docs, keywords=None) -> tmsg.StickerSet:
    """messages.StickerSet, with `packs` derived from each document's alt."""
    by_char: dict[str, list[int]] = {}
    for doc in docs:
        for attribute in doc.attributes:
            alt = getattr(attribute, "alt", None)
            if alt:
                by_char.setdefault(alt, []).append(doc.id)
    packs = [types.StickerPack(emoticon=c, documents=ids) for c, ids in by_char.items()]
    kws = [types.StickerKeyword(document_id=d, keyword=list(w))
           for d, w in (keywords or {}).items()]
    return tmsg.StickerSet(set=info, packs=packs, keywords=kws, documents=list(docs))


class FakeChannel:
    def __init__(self, title="AI engineering & Machine learning", id=1):
        self.title = title
        self.id = id


class FakeSent:
    """What Telethon hands back from send_message / send_file / get_messages."""

    _ids = itertools.count(1000)

    def __init__(self, text="", entities=None, document=None):
        self.id = next(FakeSent._ids)
        self.message = text
        self.raw_text = text
        self.entities = list(entities or [])
        self.document = document
        self.out = True
        self.sender = None
        self.date = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------- account
class FakeAccount:
    """His Telegram account: installed sets, favourites, a channel's history."""

    def __init__(self, entity=None, keeps_premium=True):
        self.entity = entity if entity is not None else FakeChannel()
        self.keeps_premium = keeps_premium
        self.emoji_sets: dict[int, tuple] = {}      # id -> (info, messages.StickerSet)
        self.sticker_sets: dict[int, tuple] = {}
        self.faved: list = []
        self.faved_packs: list = []
        # Every custom emoji Telegram knows by id, installed or not.
        self.known_emoji: dict[int, types.Document] = {}
        self.history: list = []                     # channel posts, newest first
        self.stored: dict[int, FakeSent] = {}
        self.requests: list = []                    # request objects, in order
        self.wire: list = []                        # what was actually sent
        self.reject_custom_emoji = False            # a 400 when custom emoji go out
        self.fail_read_back = False
        self.fail_documents_lookup = False
        self.empty_for_unknown = False              # unknown ids come back as DocumentEmpty
        self.fail_set_lists = False                 # getEmojiStickers / getAllStickers raise
        self.fail_history = False                   # reading the channel's posts raises
        self.broken_sets: set[int] = set()          # listed, but fetching them fails
        self.attempts = 0

    # ---- installing things
    def install_emoji_set(self, info, docs, keywords=None):
        self.emoji_sets[info.id] = (info, full_set(info, docs, keywords))
        for doc in docs:
            self.known_emoji[doc.id] = doc

    def install_sticker_set(self, info, docs, keywords=None):
        self.sticker_sets[info.id] = (info, full_set(info, docs, keywords))

    # ---- the request surface Telethon exposes
    async def __call__(self, request):
        self.requests.append(request)
        if self.fail_set_lists and isinstance(request, (
                functions.messages.GetEmojiStickersRequest,
                functions.messages.GetAllStickersRequest)):
            raise ConnectionError("set list dropped")
        if isinstance(request, functions.messages.GetEmojiStickersRequest):
            return tmsg.AllStickers(hash=1, sets=[i for i, _ in self.emoji_sets.values()])
        if isinstance(request, functions.messages.GetAllStickersRequest):
            return tmsg.AllStickers(hash=1, sets=[i for i, _ in self.sticker_sets.values()])
        if isinstance(request, functions.messages.GetStickerSetRequest):
            ref = request.stickerset
            pool = {**self.emoji_sets, **self.sticker_sets}
            if isinstance(ref, types.InputStickerSetID) and ref.id in self.broken_sets:
                raise ValueError("STICKERSET_INVALID")
            if isinstance(ref, types.InputStickerSetID) and ref.id in pool:
                return pool[ref.id][1]
            if isinstance(ref, types.InputStickerSetShortName):
                for info, full in pool.values():
                    if info.short_name == ref.short_name:
                        return full
            raise ValueError("STICKERSET_INVALID")
        if isinstance(request, functions.messages.GetCustomEmojiDocumentsRequest):
            if self.fail_documents_lookup:
                raise ConnectionError("lookup dropped")
            if self.empty_for_unknown:
                # What Telegram does for an id it has no emoji for: an empty
                # document that still carries the id it was asked about.
                return [self.known_emoji.get(i) or types.DocumentEmpty(id=i)
                        for i in request.document_id]
            return [self.known_emoji[i] for i in request.document_id if i in self.known_emoji]
        if isinstance(request, functions.messages.GetFavedStickersRequest):
            return tmsg.FavedStickers(hash=1, packs=self.faved_packs, stickers=self.faved)
        if isinstance(request, functions.messages.SaveDraftRequest):
            return True
        raise AssertionError(f"the fake does not answer {type(request).__name__}")

    async def get_me(self, input_peer=False):
        return types.User(id=42, is_self=True, premium=self.keeps_premium,
                          first_name="Jaloliddin", username="Iht_student")

    # ---- sending
    async def send_message(self, entity, message="", *, parse_mode=(),
                           formatting_entities=None, **_):
        self.attempts += 1
        entities = list(formatting_entities or [])
        has_custom = any(type(e).__name__ == "MessageEntityCustomEmoji" for e in entities)
        if has_custom and self.reject_custom_emoji:
            from telethon.errors import BadRequestError

            raise BadRequestError(request=None, message="DOCUMENT_INVALID")
        if has_custom and not self.keeps_premium:
            entities = [e for e in entities
                        if type(e).__name__ != "MessageEntityCustomEmoji"]
        sent = FakeSent(message, entities)
        self.stored[sent.id] = sent
        self.wire.append(("message", message, list(formatting_entities or []), sent))
        return sent

    async def send_file(self, entity, file, *, caption=None, parse_mode=(),
                        formatting_entities=None, **_):
        self.attempts += 1
        document = file if isinstance(file, types.Document) else None
        sent = FakeSent(caption or "", [], document=document)
        self.stored[sent.id] = sent
        self.wire.append(("file", file, [], sent))
        return sent

    async def get_messages(self, entity, limit=None, *, ids=None, **_):
        if ids is not None:
            if self.fail_read_back:
                raise ConnectionError("read-back dropped")
            return self.stored.get(ids)
        if self.fail_history:
            raise ConnectionError("history dropped")
        return list(self.history[: (limit or len(self.history))])
