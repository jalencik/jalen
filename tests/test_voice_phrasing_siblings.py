"""
"voice message" was excluded from the text-send router rule's chat name;
voice memo, voice recording, voicemail and audio message were not, and the
independent re-check found each still captured ("telegram Ali a voice memo
saying hello" asked him to confirm a text to a chat called "Ali a voice
memo"). A request for any of them goes to the brain, which has
send_voice_message; a text that only MENTIONS one still routes.
"""
from __future__ import annotations

import pytest

from jarvis.brain.router import IntentRouter
from jarvis.config import CONFIG


@pytest.fixture(scope="module")
def router():
    return IntentRouter(CONFIG)


@pytest.mark.parametrize("said", [
    "telegram Ali a voice message saying hello",
    "telegram Ali a voice note saying hello",
    "telegram Ali a voice memo saying hello",
    "telegram Ali a voice recording saying hi",
    "message Ali a voicemail saying hello",
    "send Ali an audio message saying hello",
    "send Ali an audio note saying hello",
])
def test_a_request_for_a_voice_kind_of_message_goes_to_the_brain(router, said):
    intent = router.route(said)
    assert intent is None or intent.tool != "send_telegram_message", said


@pytest.mark.parametrize("said, chat", [
    ("telegram Ali saying hello there", "Ali"),
    ("telegram Ali saying I left you a voicemail", "Ali"),
    ("telegram Ali saying I sent you a voice memo", "Ali"),
])
def test_a_text_that_only_mentions_one_still_routes(router, said, chat):
    intent = router.route(said)
    assert intent is not None and intent.tool == "send_telegram_message", said
    assert intent.args.get("to") == chat
