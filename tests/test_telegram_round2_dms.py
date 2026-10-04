"""
The second independent review of the Telegram DM and voice work, one test per
claim. Each was written and shown FAILING against the tree it was reviewing
before anything was fixed, so none of them passes for a reason that was
already true. The reviewer's findings were treated as claims, not orders; the
verdicts are in the commit message.

  1. A voice note was decoded whole before its length was checked, and the
     only length checks before that trusted the sender's own duration.
  2. send_voice_message read every language in one English voice, and he is
     written to in Uzbek and Russian.
  3. "telegram Ali a voice message saying hello" was taken by the text-send
     rule as a text to a chat called "Ali a voice message".
  4. The transcribe_voice_note spec nudged "read the chat, then transcribe" in
     one turn, which the injection guard refuses.
  5. "Groq heard nothing" was said about a clip too short to be sent to Groq.
  6. The router's list of words that are never a chat name missed the
     quantifiers, and "you ok" was filed under thanks and lol.
  7. A confirmation answer that was the question coming back through the
     microphone with one word mangled was read as a yes.
  8. A Telegram message with a link in it could not have that link followed in
     the same turn, because its fence never recorded the addresses.

Nothing here touches a network: the fakes are test_telegram_dms.py's and
test_telegram_voice.py's, and the audio is made by PyAV itself.
"""
from __future__ import annotations

import io
import time
from types import SimpleNamespace

import numpy as np
import pytest

from jalen import taint
from jalen.config import CONFIG
from jalen.safety import SafetyEngine, Tier, confirmation_question
from jalen.tools import messaging

from test_telegram_dms import (  # noqa: F401 - _clean_taint is an autouse fixture
    ALI, Dialog, FakeClient, Msg, User, _clean_taint, wire,
)
from test_telegram_voice import (  # noqa: F401 - _notice_unspent is an autouse fixture
    FakeSTT, VoiceClient, _mp3_tone, _notes_world, _notice_unspent, _speech, mp3, ogg,
)
from test_web_read_is_bounded import _Resp, net  # noqa: F401 - the fixture


@pytest.fixture
def engine():
    return SafetyEngine(CONFIG)


# =========================================================================
# 1. A voice note is counted while it is decoded, not after
# =========================================================================
def _silence_ogg(seconds: float) -> bytes:
    """Real Opus in Ogg: `seconds` of silence at 6 kbit/s, which is tiny."""
    import av

    out = io.BytesIO()
    sink = av.open(out, "w", format="ogg")
    stream = sink.add_stream("libopus", rate=48000)
    stream.layout = "mono"
    stream.bit_rate = 6000
    total, position = int(seconds * 48000), 0
    while position < total:
        step = min(48000, total - position)
        frame = av.AudioFrame.from_ndarray(
            np.zeros((1, step), dtype=np.int16), format="s16", layout="mono")
        frame.sample_rate = 48000
        frame.pts = position
        for packet in stream.encode(frame):
            sink.mux(packet)
        position += step
    for packet in stream.encode(None):
        sink.mux(packet)
    sink.close()
    return out.getvalue()


def _fake_av(monkeypatch, declared_seconds, frames):
    """
    PyAV replaced by a container that claims `declared_seconds` (None = it
    cannot say) and hands out `frames` frames of 20 ms, counting how many were
    pulled. A frame is only made when asked for, so a decoder that stops early
    leaves the rest unmade.
    """
    import av

    pulled = [0]

    class Container:
        duration = None if declared_seconds is None else int(declared_seconds * 1_000_000)
        streams = SimpleNamespace(audio=[SimpleNamespace(duration=None, time_base=None)])

        def decode(self, audio=0):
            for _ in range(frames):
                pulled[0] += 1
                yield SimpleNamespace(samples=960)

        def close(self):
            pass

    class Resampler:
        def __init__(self, **kwargs):
            pass

        def resample(self, frame):
            if frame is None:
                return []
            return [SimpleNamespace(
                samples=320, to_ndarray=lambda: np.zeros((1, 320), dtype=np.int16))]

    monkeypatch.setattr(av, "open", lambda *a, **k: Container())
    monkeypatch.setattr(av, "AudioResampler", Resampler)
    return pulled


def test_the_containers_own_length_is_read_before_a_single_frame_is_decoded(monkeypatch):
    monkeypatch.setattr(messaging, "_VOICE_NOTE_MAX_SECONDS", 10)
    pulled = _fake_av(monkeypatch, declared_seconds=3600, frames=5000)
    with pytest.raises(messaging._VoiceNoteTooLong) as caught:
        messaging._decode_voice_note(b"x", 16000)
    assert pulled[0] == 0, "frames were decoded for a note the container says is an hour long"
    assert caught.value.seconds == pytest.approx(3600)


@pytest.mark.parametrize("declared", [1, None], ids=["lies short", "cannot say"])
def test_a_container_that_lies_or_is_silent_is_stopped_at_the_cap_inside_the_loop(
        monkeypatch, declared):
    monkeypatch.setattr(messaging, "_VOICE_NOTE_MAX_SECONDS", 10)
    pulled = _fake_av(monkeypatch, declared_seconds=declared, frames=5000)   # 100 s of frames
    with pytest.raises(messaging._VoiceNoteTooLong):
        messaging._decode_voice_note(b"x", 16000)
    assert 0 < pulled[0] <= 501, (
        f"{pulled[0]} of 5000 frames were decoded: the cap is 10 s, 500 frames")


def test_a_note_inside_the_cap_decodes_as_before(monkeypatch):
    monkeypatch.setattr(messaging, "_VOICE_NOTE_MAX_SECONDS", 10)
    pulled = _fake_av(monkeypatch, declared_seconds=5, frames=250)           # 5 s
    audio = messaging._decode_voice_note(b"x", 16000)
    assert pulled[0] == 250 and len(audio) == 250 * 320
    assert audio.dtype.name == "float32"


def test_real_opus_silence_that_is_over_the_cap_is_refused_on_its_declared_length(monkeypatch):
    monkeypatch.setattr(messaging, "_VOICE_NOTE_MAX_SECONDS", 5)
    data = _silence_ogg(30)
    assert len(data) < 40_000, "30 s of silence is tiny: the size cap cannot see it"
    with pytest.raises(messaging._VoiceNoteTooLong) as caught:
        messaging._decode_voice_note(data, 16000)
    assert caught.value.seconds == pytest.approx(30, abs=1)


def test_real_opus_silence_whose_container_lies_stops_at_the_cap(monkeypatch):
    """The real decoder, with the container's own length unreadable."""
    import av

    monkeypatch.setattr(messaging, "_VOICE_NOTE_MAX_SECONDS", 5)
    monkeypatch.setattr(messaging, "_declared_seconds", lambda container: 0.0)
    resampled = [0]
    real = av.AudioResampler

    class Counting:                       # the real resampler, which cannot be subclassed
        def __init__(self, **kwargs):
            self._real = real(**kwargs)

        def resample(self, frame):
            out = self._real.resample(frame)
            resampled[0] += sum(o.samples for o in out)
            return out

    monkeypatch.setattr(av, "AudioResampler", Counting)
    with pytest.raises(messaging._VoiceNoteTooLong):
        messaging._decode_voice_note(_silence_ogg(60), 16000)
    assert resampled[0] <= 5.2 * 16000, (
        f"{resampled[0] / 16000:.1f} s were decoded of a 60 s file with a 5 s cap")


def test_a_hostile_voice_note_is_refused_in_a_sentence_and_nothing_goes_to_groq(
        monkeypatch):
    """The sender says 2 s (Telegram's duration is the sender's word); the audio is 90 s."""
    monkeypatch.setattr(messaging, "_VOICE_NOTE_MAX_SECONDS", 20)
    client = wire(monkeypatch, VoiceClient(dialogs=[Dialog(ALI)], chats={ALI.id: [
        Msg(1, "", sender=ALI, kind="voice", duration=2, size=9000)]},
        audio=_silence_ogg(90)))
    monkeypatch.setattr(messaging, "_groq_ready", lambda: True)
    monkeypatch.setattr(messaging, "_transcriber", lambda s: pytest.fail("Groq was called"))
    reply = messaging.transcribe_voice_note(chat="Ali Karimov")
    assert "nothing was sent" in reply and "I only send up to 0:20" in reply
    assert "Traceback" not in reply and "TooLong" not in reply
    assert len(client.downloads) == 1


def test_the_number_of_bytes_that_arrive_is_checked_not_only_the_number_declared(monkeypatch):
    monkeypatch.setattr(messaging, "_VOICE_NOTE_MAX_BYTES", 1000)
    client = wire(monkeypatch, VoiceClient(dialogs=[Dialog(ALI)], chats={ALI.id: [
        Msg(1, "", sender=ALI, kind="voice", duration=2, size=500)]},
        audio=b"\0" * 5000))
    monkeypatch.setattr(messaging, "_groq_ready", lambda: True)
    monkeypatch.setattr(messaging, "_decode_voice_note",
                        lambda *a, **k: pytest.fail("5000 bytes were decoded, cap 1000"))
    monkeypatch.setattr(messaging, "_transcriber", lambda s: pytest.fail("Groq was called"))
    reply = messaging.transcribe_voice_note(chat="Ali Karimov")
    assert "nothing was sent" in reply
    assert len(client.downloads) == 1


# =========================================================================
# 2. The voice follows the language
# =========================================================================
@pytest.mark.parametrize("text, language, expect", [
    ("Hello, I will be late", "", "en"),
    ("Привет, я опоздаю на десять минут", "", "ru"),
    ("Ўзбекистонда қандай ҳол", "", "uz"),
    ("Salom, men o'n daqiqa kechikaman", "uz", "uz"),
    ("Salom", "Uzbek", "uz"),
    ("Hello", "Russian", "ru"),
    ("Привет", "en", "en"),
    ("Hello", "klingon", "en"),
    ("Привет", "klingon", "ru"),
    ("ok, до встречи", "", "ru"),
    ("5 + 5 = 10", "", "en"),
])
def test_the_language_is_the_one_named_else_read_from_the_script(text, language, expect):
    from jalen import voicelang

    assert voicelang.resolve(text, language) == expect


def test_each_language_has_a_name_to_say_in_the_question():
    from jalen import voicelang

    assert voicelang.name("uz") == "Uzbek"
    assert voicelang.name("ru") == "Russian"
    assert voicelang.name("en") == "English"


def test_the_config_names_a_real_voice_for_uzbek_and_russian_and_says_how_it_is_known():
    """
    The names were read from edge_tts.list_voices() on 2026-10-01 (322 voices;
    uz-UZ: MadinaNeural and SardorNeural, ru-RU: DmitryNeural and
    SvetlanaNeural). The male ones, because Jalen's own voice is male.
    """
    assert CONFIG.get_path("telegram.personal.voice_message.voices.uz") == "uz-UZ-SardorNeural"
    assert CONFIG.get_path("telegram.personal.voice_message.voices.ru") == "ru-RU-DmitryNeural"
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "config" / "jalen.yaml").read_text(
        encoding="utf-8")
    at = text.index("voices:")
    block = text[max(0, at - 1500): at + 400]
    assert "list_voices" in block and "NOT MEASURED" in block


def _capture_voice(monkeypatch):
    seen = []

    async def fake(self, text):
        seen.append((self.voice, text))
        return b"mp3"

    monkeypatch.setattr("jalen.audio.tts.Speaker._synthesise", fake)
    return seen


@pytest.mark.parametrize("text, language, voice", [
    ("Привет", "", "ru-RU-DmitryNeural"),
    ("Salom", "uz", "uz-UZ-SardorNeural"),
    ("Hello", "", None),                       # None: the voice Jalen speaks with
])
def test_the_speech_engine_is_given_the_voice_for_the_language(monkeypatch, text, language, voice):
    seen = _capture_voice(monkeypatch)
    assert messaging._speech_mp3(text, language) == b"mp3"
    expected = voice or CONFIG.get_path("tts.voice")
    assert seen[-1] == (expected, text)


def test_a_cyrillic_message_is_spoken_in_the_russian_voice_without_being_told(monkeypatch, mp3):
    client = wire(monkeypatch, VoiceClient(dialogs=[Dialog(ALI)]))
    asked = []
    monkeypatch.setattr(messaging, "_speech_mp3",
                        lambda words, language="": asked.append((words, language)) or mp3)
    reply = messaging.send_voice_message(to="Ali Karimov", text="Привет, я опоздаю")
    assert asked == [("Привет, я опоздаю", "ru")]
    assert reply.startswith("Sent a voice message") and "Russian" in reply
    assert len(client.files) == 1


def test_a_named_language_is_passed_to_the_voice_and_said_in_the_reply(monkeypatch, mp3):
    wire(monkeypatch, VoiceClient(dialogs=[Dialog(ALI)]))
    asked = []
    monkeypatch.setattr(messaging, "_speech_mp3",
                        lambda words, language="": asked.append(language) or mp3)
    reply = messaging.send_voice_message(to="Ali Karimov", text="Salom, kechikaman",
                                         language="uz")
    assert asked == ["uz"] and "Uzbek" in reply


def test_an_english_message_is_unchanged_and_says_no_language(monkeypatch, mp3):
    wire(monkeypatch, VoiceClient(dialogs=[Dialog(ALI)]))
    asked = []
    monkeypatch.setattr(messaging, "_speech_mp3",
                        lambda words, language="": asked.append(language) or mp3)
    reply = messaging.send_voice_message(to="Ali Karimov", text="see you at six")
    assert asked == ["en"]
    assert "Russian" not in reply and "Uzbek" not in reply and "English" not in reply


def _question(engine, **args):
    verdict = engine.classify("send_voice_message", {"to": "Ali Karimov", **args})
    return confirmation_question(verdict, CONFIG.get_path("safety_tiers.red.confirm_template"))


def test_the_question_says_when_the_words_are_not_english(engine):
    russian = _question(engine, text="Привет, я опоздаю")
    assert "Russian" in russian and "Привет, я опоздаю" in russian
    uzbek = _question(engine, text="Salom, kechikaman", language="uz")
    assert "Uzbek" in uzbek and "Salom, kechikaman" in uzbek
    assert russian.endswith("Confirm?") and uzbek.endswith("Confirm?")


def test_the_question_for_english_words_is_the_one_it_always_was(engine):
    english = _question(engine, text="I will be ten minutes late")
    assert "Russian" not in english and "Uzbek" not in english
    assert "send Ali Karimov a voice message in Jalen's synthetic voice, saying:" in english


def test_the_spec_offers_a_language_and_says_when_to_use_it():
    from jalen.brain.tools import TOOL_SPECS

    description, params = TOOL_SPECS["send_voice_message"]
    assert set(params) == {"to", "text", "language"}
    assert params["language"][2] is False
    lowered = description.lower()
    assert "uzbek" in lowered and "russian" in lowered and "latin" in lowered


# =========================================================================
# 3. "a voice message" is not a chat name
# =========================================================================
def _route(phrase):
    from jalen.brain.router import IntentRouter

    return IntentRouter(CONFIG).route(phrase)


@pytest.mark.parametrize("phrase", [
    "telegram Ali a voice message saying hello",
    "text Ali a voice message saying hello",
    "dm Ali a voice note saying hello",
    "message Ali a voice message that I am late",
    "send to Ali a voice message saying hello",
    "send Ali a voice message on telegram saying hello",
    "write Ali a voice note on telegram that I am late",
    "telegram Ali Karimov a voice message with the message hello",
])
def test_a_voice_message_request_is_never_a_text_to_a_chat_with_that_name(phrase):
    hit = _route(phrase)
    assert hit is None or hit.tool != "send_telegram_message", (phrase, hit)


@pytest.mark.parametrize("phrase, to, text", [
    ("telegram Ali saying I left you a voice message", "Ali", "I left you a voice message"),
    ("text Rodion that the voice note is in the folder", "Rodion", "the voice note is in the folder"),
    ("telegram Rodion saying I'll be late", "Rodion", "I'll be late"),
    ("message sat talk on telegram saying hi", "sat talk", "hi"),
])
def test_a_text_that_only_mentions_a_voice_message_still_routes(phrase, to, text):
    hit = _route(phrase)
    assert hit is not None and hit.tool == "send_telegram_message", phrase
    assert hit.args["to"] == to and hit.args["text"] == text


# =========================================================================
# 4. The spec does not nudge a read followed by a transcription
# =========================================================================
def test_the_transcribe_spec_says_it_is_for_his_own_request_not_after_a_read():
    from jalen.brain.tools import TOOL_SPECS

    description = TOOL_SPECS["transcribe_voice_note"][0]
    lowered = description.lower()
    assert "his own request" in lowered
    assert "latest" in lowered and "a number he gave" in lowered
    assert "after reading the chat" in lowered and "same turn" in lowered


@pytest.fixture(scope="module")
def prompt() -> str:
    from jalen.brain.agent import Brain

    async def noop(*a, **k):
        return True

    return " ".join(Brain(CONFIG, SafetyEngine(CONFIG), None, confirm=noop,
                          announce=noop).system_prompt().lower().split())


def test_the_prompt_says_the_same_about_transcribing_and_about_the_language(prompt):
    assert "not right after reading the chat in the same turn" in prompt
    assert "on his own request" in prompt
    assert "pass language ('uz' or 'ru')" in prompt


def test_that_is_what_the_gate_does_after_a_read(engine, monkeypatch):
    """Not a new rule: the reason the sentence is in the spec."""
    wire(monkeypatch, FakeClient(dialogs=[Dialog(ALI)], chats={ALI.id: [
        Msg(4, "hi", sender=ALI)]}))
    messaging.read_telegram(chat="Ali Karimov")
    verdict = engine.classify("transcribe_voice_note", {"chat": "Ali Karimov", "which": "#4"},
                              origin=taint.origin_now())
    assert verdict.tier is Tier.BLACK and verdict.blocked


# =========================================================================
# 5. A clip too short to send is not "heard nothing"
# =========================================================================
def test_the_floor_is_the_one_the_transcriber_applies():
    """Pinned to the real Transcriber, so the two cannot drift apart."""
    from jalen.audio.stt import Transcriber
    from jalen.config import SECRETS

    assert messaging._VOICE_NOTE_MIN_SECONDS == 0.2
    stt = Transcriber(CONFIG, SECRETS)
    calls = []
    stt._via_groq = lambda audio: calls.append(len(audio)) or "words"
    stt.primary = stt.fallback = "groq"
    rate = stt.sample_rate
    assert stt.transcribe(np.zeros(int(rate * 0.19), dtype=np.float32)) == ""
    assert calls == [], "the transcriber sent a clip under the floor"
    assert stt.transcribe(np.zeros(int(rate * 0.21), dtype=np.float32)) == "words"
    assert calls == [int(rate * 0.21)]


def test_a_voice_note_under_a_fifth_of_a_second_says_too_short_and_sends_nothing(
        monkeypatch):
    from jalen.tools import messaging as m

    short, _ = m._to_voice_note(_mp3_tone(0.12))
    _notes_world(monkeypatch, short)
    monkeypatch.setattr(m, "_transcriber", lambda s: pytest.fail("Groq was called"))
    reply = messaging.transcribe_voice_note(chat="Ali Karimov")
    assert "too short" in reply and "nothing was sent" in reply
    assert "heard nothing" not in reply


def test_a_long_enough_silent_voice_note_still_says_groq_heard_nothing(monkeypatch, ogg):
    _notes_world(monkeypatch, ogg, words="   ")
    reply = messaging.transcribe_voice_note(chat="Ali Karimov")
    assert "heard nothing" in reply and "too short" not in reply


# =========================================================================
# 6. The router's pronouns, and "you ok"
# =========================================================================
QUANTIFIERS = ["no one", "none of them", "all of them", "both", "whoever", "whatever",
               "something", "anything", "everything", "nothing", "noone", "either",
               "neither", "some", "any", "each", "many", "few", "several"]


@pytest.mark.parametrize("word", QUANTIFIERS)
@pytest.mark.parametrize("shape", ["what did {} say on telegram", "did {} reply on telegram",
                                   "did {} write back on telegram"])
def test_a_quantifier_is_never_taken_for_the_name_of_a_chat(word, shape):
    hit = _route(shape.format(word))
    assert hit is None or hit.tool != "read_telegram", (shape.format(word), hit)


@pytest.mark.parametrize("word", ["no one", "none of them", "everything", "both"])
def test_read_my_telegram_from_a_quantifier_is_not_a_chat_either(word):
    hit = _route(f"read my telegram from {word}")
    assert hit is None or hit.tool != "read_telegram", word


@pytest.mark.parametrize("name", ["ali", "anya", "nodir", "somon", "allison", "eachan"])
def test_a_name_that_only_starts_with_the_same_letters_still_reaches_its_chat(name):
    """The pattern is a whole word: 'Anya' is not 'any', 'Nodir' is not 'no one'."""
    hit = _route(f"did {name} write back on telegram")
    assert hit is not None and hit.tool == "read_telegram" and hit.args["chat"] == name


@pytest.mark.parametrize("said", ["you ok", "you good", "you fine", "You okay", "you alright",
                                  "you there", "u ok", "you ok bro", "you good man"])
def test_a_check_in_is_not_a_closing_word(said):
    assert not messaging._acknowledgement(said), said


@pytest.mark.parametrize("said", ["ok", "thank you", "thank you so much", "thanks bro",
                                  "you're welcome", "good night", "see you", "no problem bro",
                                  "ok thank you", "thank you, good", "all good"])
def test_the_closers_that_mention_you_are_still_closers(said):
    assert messaging._acknowledgement(said), said


def test_a_friend_who_only_asked_if_he_is_ok_is_listed_not_counted(monkeypatch):
    friend = User(130, "Checking", "Friend")
    wire(monkeypatch, FakeClient(dialogs=[
        Dialog(friend, unread=0, last=Msg(1, "you ok", sender=friend, minutes_ago=30))]))
    from test_telegram_dms import _between_fence

    inside, _ = _between_fence(messaging.telegram_dm_catchup())
    assert "Checking Friend" in inside


# =========================================================================
# 7. His answer to a question is not the question coming back
# =========================================================================
def _heard_while_asking(question, heard, asked_ago=0.5):
    from test_confirmations_are_bound import _jalen, _pending, _say

    j = _jalen()
    _pending(j, question=question, asked_ago=asked_ago)
    _say(j, heard)
    return j


VOICE_QUESTION = ("send Ali Karimov a voice message in Jalen's synthetic voice, saying: "
                  "ok sounds good, see you at six. Confirm?")


def test_the_question_coming_back_with_a_word_mangled_does_not_approve_itself():
    j = _heard_while_asking(VOICE_QUESTION, "ok sound good see you at six confirm")
    assert j._answer_q.empty(), "Jalen approved its own question"
    assert not j.routed


@pytest.mark.parametrize("heard", [
    "send ali carry mov a voice message in jalen's synthetic voice saying ok sounds good",
    "voice message in jalen synthetic voice saying ok sounds good see you at six confirm",
    "ali karimov a voice message in jalen's synthetic voice",
    "ok sound good see you at six confirm",
])
def test_a_leaky_echo_of_the_question_is_ignored_in_silence(heard):
    """Not approved, not routed, and not answered with "was that a yes or a no?"
    either: that sentence is itself something the microphone would hear."""
    j = _heard_while_asking(VOICE_QUESTION, heard)
    assert j._answer_q.empty(), heard
    assert not j.routed and j.said == [], (heard, j.said)


@pytest.mark.parametrize("heard", [
    "yes", "yes please", "yes go ahead and send it", "no don't send it",
    "yeah send it to him", "do it", "yes that is right go ahead",
    "no wait send it to Rodion instead",
])
def test_his_real_answers_inside_the_echo_window_still_count(heard):
    j = _heard_while_asking(VOICE_QUESTION, heard)
    assert not j._answer_q.empty(), f"{heard!r} was taken for an echo"


def test_a_yes_that_repeats_a_few_words_of_the_question_still_counts():
    """3 of 6 words are in the question: well under the 80% that makes an echo."""
    j = _heard_while_asking(VOICE_QUESTION, "yes send it to Ali Karimov")
    assert not j._answer_q.empty()


def test_an_echo_that_arrives_after_the_endpoint_and_the_transcription_is_still_one():
    """
    The echo of a question reaches the gate after the endpoint's silence and
    the transcription - 3.2 s at the median - so a window of ECHO_TAIL_S alone
    would have caught almost none of them. Judged over the doors' window.
    """
    from jalen.app import ECHO_TAIL_S

    for asked_ago in (ECHO_TAIL_S + 0.5, 6.0, 11.0):
        j = _heard_while_asking(VOICE_QUESTION, "ok sound good see you at six confirm",
                                asked_ago=asked_ago)
        assert j._answer_q.empty() and j.said == [], asked_ago


def test_after_that_window_the_same_words_are_him():
    from jalen.app import ECHO_REACHES_THE_GATE_S

    j = _heard_while_asking(VOICE_QUESTION, "ok sound good see you at six confirm",
                            asked_ago=ECHO_REACHES_THE_GATE_S + 1)
    assert not j._answer_q.empty()


def test_a_one_word_tail_is_the_echo_for_the_whole_window_not_just_three_seconds():
    """
    This test used to say "confirm" four seconds after the question is a yes,
    "as it always was". The independent re-check reproduced that approving a
    voice message: the echo of a question reaches the gate about 3.2 s after
    it ends, so a one-word tail arriving after the exact-tail window WAS the
    echo. Now judged over the whole window; "yes" is the answer
    (tests/test_confirmation_echo_round_two.py).
    """
    from jalen.app import ECHO_TAIL_S

    j = _heard_while_asking(VOICE_QUESTION, "confirm", asked_ago=ECHO_TAIL_S + 1)
    assert j._answer_q.empty()


def test_a_one_word_confirm_after_the_whole_window_is_a_real_yes():
    from jalen.app import ECHO_REACHES_THE_GATE_S

    j = _heard_while_asking(VOICE_QUESTION, "confirm", asked_ago=ECHO_REACHES_THE_GATE_S + 1)
    assert not j._answer_q.empty()


def _measure():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "measure_address_gate.py"
    spec = importlib.util.spec_from_file_location("measure_address_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_real_log_none_of_his_answers_to_a_confirmation_is_taken_for_its_echo():
    """
    The measurement quoted at _CONFIRM_ECHO_SHARE in jalen/app.py, runnable:
    every spoken "...Confirm?" in the log and the next thing he said. Skipped
    where there is no log ($JALEN_AUDIT_LOG, or data/audit.jsonl).
    """
    import re

    import jalen.app as app

    module = _measure()
    log = module.audit_log_path()
    if not log.exists():
        pytest.skip("no data/audit.jsonl on this machine")
    rows = module.load(log)
    word = re.compile(r"[\w']+", re.UNICODE)
    questions = answers = long_answers = 0
    worst = 0.0
    for i, row in enumerate(rows):
        if (row.get("kind") != "utterance" or module._details(row).get("who") != "jarvis"
                or not str(row.get("summary", "")).strip().lower().endswith("confirm?")):
            continue
        questions += 1
        asked = set(word.findall(row["summary"].lower()))
        for later in rows[i + 1: i + 8]:
            if later.get("kind") == "utterance" and module._details(later).get("who") == "user":
                heard = word.findall(str(later["summary"]).lower())
                answers += 1
                if len(heard) >= app._CONFIRM_ECHO_MIN_WORDS:
                    long_answers += 1
                    worst = max(worst, sum(w in asked for w in heard) / len(heard))
                break
    if questions < 20:
        pytest.skip("the log has been trimmed below the 22 confirmation questions measured")
    assert worst < app._CONFIRM_ECHO_SHARE, (
        f"{worst:.2f} of a real answer is in its own question ({long_answers} answers, "
        f"{answers} of {questions} questions)")


def test_the_leaky_rule_carries_its_measurement_and_is_the_one_the_gate_uses():
    """
    Four words and 80%: what _sounds_like_its_own_voice measured on the whole
    corpus. Against his 20 real answers to the 22 spoken confirmation
    questions in data/audit.jsonl (read 2026-10-01) none is flagged; the
    largest share of a four-word-or-longer answer found in its own question
    is 0.5.
    """
    import inspect

    import jalen.app as app

    assert app._CONFIRM_ECHO_MIN_WORDS == 4 and app._CONFIRM_ECHO_SHARE == 0.80
    source = inspect.getsource(app)
    at = source.index("_CONFIRM_ECHO_SHARE =")
    comment = source[max(0, at - 1500): at]
    assert "audit.jsonl" in comment and "20 " in comment and "22 " in comment


# =========================================================================
# 8. A link in a Telegram message can be followed in the same turn
# =========================================================================
def _page(text="<html><title>t</title><p>the article</p></html>"):
    return _Resp(body=text.encode())


def test_a_link_in_a_message_he_asked_to_have_read_can_be_followed_in_that_turn(
        monkeypatch, net):
    from jalen.tools import research

    wire(monkeypatch, FakeClient(dialogs=[Dialog(ALI)], chats={ALI.id: [
        Msg(4, "the paper is at https://papers.example/abs/2410.00001 have a look",
            sender=ALI)]}))
    messaging.read_telegram(chat="Ali Karimov")
    assert taint.is_tainted()
    net.responses = [_page()]
    out = research.web_read("https://papers.example/abs/2410.00001")
    assert "the article" in out
    assert net.seen_urls == ["https://papers.example/abs/2410.00001"]


def test_an_address_the_message_did_not_show_is_still_refused(monkeypatch, net):
    from jalen.tools import research

    wire(monkeypatch, FakeClient(dialogs=[Dialog(ALI)], chats={ALI.id: [
        Msg(4, "the paper is at https://papers.example/abs/2410.00001", sender=ALI)]}))
    messaging.read_telegram(chat="Ali Karimov")
    net.responses = [_page()]
    out = research.web_read("https://papers.example/abs/2410.00001?d=" + "x" * 300)
    assert net.seen_urls == [], "an address built from what was read was fetched"
    assert "yourself" in out.lower()


def test_a_link_in_a_voice_note_transcript_or_a_search_hit_is_followable_too(
        monkeypatch, net, ogg):
    from jalen.tools import research

    _notes_world(monkeypatch, ogg, words="read https://news.example/story-9 before the call")
    messaging.transcribe_voice_note(chat="Ali Karimov")
    net.responses = [_page()]
    assert "the article" in research.web_read("https://news.example/story-9")


def test_a_link_in_a_search_hit_or_a_sticker_title_fence_is_recorded_the_same_way(monkeypatch):
    """Every caller of messaging._fence goes through the one door."""
    taint.he_asked_again()
    messaging._fence("see https://x.example/a", "his Telegram sticker packs", limit=8000)
    assert taint.url_was_read("https://x.example/a")
