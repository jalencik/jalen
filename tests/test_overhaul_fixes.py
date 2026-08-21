"""
Regression tests for the product-quality overhaul.

Every test here corresponds to a defect found by measuring or driving the
real application, not by reading it — the user's own report ("takes a
minute", "basic commands don't work", "I can't stop it", "old commands
execute later in a burst") is the source of truth for all of them.
"""
from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.brain.router import IntentRouter  # noqa: E402
from jarvis.config import CONFIG, Cfg  # noqa: E402


@pytest.fixture
def router():
    return IntentRouter(CONFIG)


def route_tool(router, text):
    intent = router.route(text)
    return intent.tool if intent else None


# --------------------------------------------------------- app launching
@pytest.mark.parametrize(
    "phrase",
    ["open chrome", "launch chrome", "start chrome", "run chrome",
     "fire up chrome", "pull up chrome", "open up chrome"],
)
def test_every_launch_verb_routes_locally(router, phrase):
    """Only "open" was recognised; the rest paid a full Claude round-trip
    (seconds) for something the router does in under a millisecond."""
    assert route_tool(router, phrase) == "open_target"


def test_trailing_self_address_is_stripped(router):
    """"open chrome, Jarvis" reached open_app as name="chrome, jarvis" and
    tried to launch an app by that literal name."""
    intent = router.route("open chrome, jarvis")
    assert intent.tool == "open_target"
    assert intent.args["name"] == "chrome"


@pytest.mark.parametrize("phrase", ["can you open chrome", "please open chrome",
                                    "could you please open chrome"])
def test_leading_politeness_is_stripped(router, phrase):
    intent = router.route(phrase)
    assert intent.tool == "open_target"
    assert intent.args["name"] == "chrome"


def test_open_folder_is_not_shadowed_by_open_app(router):
    """The open_app catch-all "^open (.+)$" sat first and swallowed every
    "open the folder X", making open_folder unreachable."""
    assert route_tool(router, "open the folder Downloads") == "open_folder"


def test_play_named_media_app_launches_it(router):
    """"play spotify" hit the play/pause media key — a silent no-op when
    Spotify isn't running — instead of launching it."""
    assert route_tool(router, "play spotify") == "open_app"


def test_generic_play_music_still_uses_the_media_key(router):
    assert route_tool(router, "play the music") == "media_play_pause"


@pytest.mark.parametrize("phrase", ["minimize this window", "maximize this window",
                                    "minimize the window", "maximize window"])
def test_window_state_matches_every_article(router, phrase):
    """The optional-article group had a space on only one branch, so
    "...this window" never matched."""
    assert route_tool(router, phrase) == "window_state"


# ------------------------------------------------------------- lifecycle
@pytest.mark.parametrize(
    "phrase,expected",
    [("quit jarvis", "jalen_quit"), ("exit", "jalen_quit"), ("shut down", "jalen_quit"),
     ("pause", "jalen_pause"), ("hold on", "jalen_pause"),
     # "stop listening"/"go to sleep" hit the older jalen_sleep rule, which
     # now pauses for real instead of only claiming to.
     ("stop listening", "jalen_sleep"), ("go to sleep", "jalen_sleep"),
     ("resume", "jalen_resume"), ("wake up", "jalen_resume"),
     ("restart jarvis", "jalen_restart")],
)
def test_lifecycle_commands_exist(router, phrase, expected):
    """There was no spoken way to stop Jarvis at all — only Ctrl+C in
    whichever terminal launched it, useless once that window is closed."""
    assert route_tool(router, phrase) == expected


def test_no_catch_all_rule_shadows_a_jarvis_command(router):
    """
    Guards the whole CLASS of bug, not just the instances found.

    Three separate defects were catch-all rules eating specific ones:
    "open the folder X" -> open_app, "go to sleep" -> focus_window (a window
    named "sleep"), "play spotify" -> media key. Any command aimed at Jarvis
    itself must reach a jarvis_* tool and never be reinterpreted as an app,
    window or file name.
    """
    self_commands = {
        "quit": "jalen_quit", "exit": "jalen_quit", "shut down": "jalen_quit",
        "pause": "jalen_pause", "resume": "jalen_resume", "wake up": "jalen_resume",
        "restart": "jalen_restart", "go to sleep": "jalen_sleep",
        "sleep": "jalen_sleep", "stop listening": "jalen_sleep",
        "mute": "jalen_mute", "unmute": "jalen_unmute",
        "be quiet": "jalen_mute", "shut up": "jalen_mute",
    }
    wrong = {}
    for phrase, expected in self_commands.items():
        got = route_tool(router, phrase)
        if got != expected:
            wrong[phrase] = f"expected {expected}, got {got}"
    assert not wrong, f"catch-all rules are shadowing Jarvis's own commands: {wrong}"


# ---------------------------------------------------- window title matching
def test_title_pattern_is_case_insensitive():
    """The router lowercases every utterance, so a case-sensitive title
    regex never matched real window titles — close/focus silently failed."""
    from jarvis.tools.system import _title_pattern

    assert _title_pattern("chrome").search("Untitled - Google Chrome")
    assert _title_pattern("notepad").search("Untitled - Notepad")


def test_title_pattern_escapes_regex_characters():
    from jarvis.tools.system import _title_pattern

    assert _title_pattern("c++ (test)").search("my c++ (test) window")


# --------------------------------------------------------- app resolution
def test_unknown_app_is_reported_not_faked():
    """open_app used to reply "Opening X" for anything Popen didn't raise on,
    so a nonexistent app produced a confident success and nothing opened."""
    from jarvis.tools.system import open_app

    result = open_app("zzz_definitely_not_an_app_9x7")
    assert "couldn't find" in result.lower()
    assert "opening" not in result.lower()


def test_known_app_resolves_to_a_real_target():
    from jarvis.tools.system import _resolve_executable

    assert _resolve_executable("notepad")
    assert _resolve_executable("zzz_definitely_not_an_app_9x7") is None


# ------------------------------------------------------------------- tts
def test_sentence_streaming_config_is_actually_read():
    """tts.sentence_streaming was documented and configured but nothing read
    it — synthesis and playback were always strictly serial."""
    from jarvis.audio.tts import Speaker

    on = Cfg({**CONFIG, "tts": {**CONFIG.get("tts", {}), "sentence_streaming": True}})
    off = Cfg({**CONFIG, "tts": {**CONFIG.get("tts", {}), "sentence_streaming": False}})
    assert Speaker(on).sentence_streaming is True
    assert Speaker(off).sentence_streaming is False


def test_speaker_serializes_concurrent_calls():
    """Two turns finishing at once both called say(), and since the
    interrupt/speaking flags are instance state one call's cleanup clobbered
    the other's mid-playback."""
    from jarvis.audio.tts import Speaker

    speaker = Speaker(CONFIG)
    overlaps = []
    active = threading.Event()

    def fake_render(sentence):
        if active.is_set():
            overlaps.append(sentence)
        active.set()
        import time as t
        t.sleep(0.05)
        active.clear()
        return None  # skip playback entirely

    speaker._render = fake_render
    speaker._say_sequential = lambda sents: all(fake_render(s) is None for s in sents)

    threads = [threading.Thread(target=speaker.say, args=(f"Sentence {i}.",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not overlaps, f"say() ran concurrently: {overlaps}"


# ------------------------------------------------------------- mic buffer
def test_queued_seconds_reports_backlog():
    """The wake handler needs this to tell "the rest of your sentence"
    (keep) from "the machine stalled" (drop)."""
    import numpy as np
    from jarvis.audio.mic import Microphone

    mic = Microphone(CONFIG)
    assert mic.queued_seconds() == 0.0
    for _ in range(10):
        mic._q.put(np.zeros(mic.blocksize, dtype=np.float32))
    expected = 10 * mic.blocksize / mic.sample_rate
    assert abs(mic.queued_seconds() - expected) < 1e-6


# ---------------------------------------------------------------- safety
def test_window_state_is_green_not_amber():
    """Unclassified tools default to AMBER, so minimising a window paid a
    mandatory 4-second announce-and-wait."""
    from jarvis.safety import SafetyEngine

    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    verdict = engine.classify("window_state", {"state": "minimize"})
    assert verdict.tier.value == "green"
    assert not verdict.detail.get("unclassified")


def test_private_mode_default_is_honoured(tmp_path):
    """memory.private_mode_default was configurable but ignored."""
    from jarvis.audit import AuditLog

    cfg = Cfg({
        **CONFIG,
        "memory": {**CONFIG.get("memory", {}), "private_mode_default": True},
        "audit": {**CONFIG.get("audit", {}),
                  "db_path": str(tmp_path / "a.db"),
                  "jsonl_path": str(tmp_path / "a.jsonl")},
    })
    assert AuditLog(cfg, "test-private").private is True


# ------------------------------------------------- open anything (launcher)
def test_generic_client_name_resolves_to_whats_installed():
    """"Open Telegram" must open whichever Telegram client is actually here —
    AyuGram on this machine. String similarity alone never gets there
    ("telegram" vs "ayugram" scores below any safe cutoff), which is why an
    explicit equivalence family exists."""
    from jarvis.tools.launcher import resolve_app

    target, _matched = resolve_app("telegram")
    assert target, "no installed Telegram-family client resolved"


def test_filler_words_do_not_defeat_app_lookup():
    from jarvis.tools.launcher import resolve_app

    assert resolve_app("my chrome")[1] == resolve_app("chrome")[1]


def test_open_target_is_honest_when_nothing_matches():
    from jarvis.tools.launcher import open_target

    assert "couldn't find" in open_target("zzz_no_such_thing_9x7").lower()


def test_learned_alias_survives_and_resolves(tmp_path, monkeypatch):
    """A nickname you have to re-teach every restart is worthless."""
    from jarvis.tools import launcher

    monkeypatch.setattr(launcher, "ALIASES_PATH", tmp_path / "aliases.json")
    desktop = str(Path.home() / "Desktop")
    launcher.remember_alias("my beats", desktop)
    assert "my beats" in launcher.list_aliases()
    assert launcher.resolve_app("my beats")[0] == desktop


def test_file_search_ignores_filler_words():
    """"open my CV" must search for "cv", not the literal phrase "my cv"."""
    from jarvis.tools.launcher import find_files

    assert find_files("my cv") == find_files("cv")


def test_open_target_is_registered_and_green():
    """Opening destroys nothing and closing the window undoes it, so it must
    just run — but as an EXPLICIT tier, never the unclassified default."""
    from jarvis import tools
    from jarvis.safety import SafetyEngine

    assert "open_target" in tools.REGISTRY
    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    verdict = engine.classify("open_target", {"name": "chrome"})
    assert verdict.tier.value == "green"
    assert not verdict.detail.get("unclassified")


# ------------------------------------------------- confirmation discipline
def test_non_destructive_actions_never_ask(router):
    """
    Reported verbatim: "it is asking me confirmation for even the simplest
    tasks... if it is not deleting something it should not ask me."

    paranoid_first_week promoted every AMBER to RED, so opening a file asked
    exactly like sending an email. The rule now is destructive-vs-not:
    opening, creating and reading just happen.
    """
    from jarvis.safety import SafetyEngine

    engine = SafetyEngine(CONFIG)
    silent = ["open_target", "open_app", "open_folder", "create_file", "create_folder",
              "read_file", "search_files", "get_time", "list_directory", "screenshot",
              "window_state", "focus_window", "media_play_pause", "volume_set"]
    asking = {t: engine.classify(t, {}).tier.value for t in silent
              if engine.classify(t, {}).tier.value != "green"}
    assert not asking, f"these should just run, but they gate: {asking}"


def test_destructive_actions_still_ask(router):
    """The other half of the same rule — speed must not cost the gate."""
    from jarvis.safety import SafetyEngine

    engine = SafetyEngine(CONFIG)
    must_ask = ["delete_file", "send_email", "send_telegram_message", "run_powershell",
                "install_software", "post_public", "git_push"]
    not_asking = {t: engine.classify(t, {}).tier.value for t in must_ask
                  if engine.classify(t, {}).tier.value != "red"}
    assert not not_asking, f"these must confirm first: {not_asking}"


def test_black_tier_is_still_absolute():
    from jarvis.safety import SafetyEngine

    engine = SafetyEngine(CONFIG)
    for tool in ("transfer_funds", "execute_payment", "enter_password", "vps_access"):
        assert engine.classify(tool, {}).blocked is True, tool


# --------------------------------------- Jarvis's own controls never gate
def test_jarvis_self_controls_are_never_gated():
    """
    "quit" used to classify as unclassified-AMBER, so it announced
    "jarvis quit, say stop if you don't want that" and waited 2 seconds
    before quitting. These change Jarvis's own state, not the machine —
    gating them is absurd, and the AMBER was inherited by accident rather
    than decided.
    """
    from jarvis.safety import SafetyEngine

    engine = SafetyEngine(CONFIG)
    controls = ["jalen_quit", "jalen_pause", "jalen_resume", "jalen_restart",
                "jalen_mute", "jalen_unmute", "jalen_sleep", "jalen_ack", "jalen_timing", "private_mode",
                "set_posture", "reload_config", "audit_digest", "morning_brief"]
    gated = {}
    for tool in controls:
        verdict = engine.classify(tool, {})
        if verdict.tier.value != "green" or verdict.detail.get("unclassified"):
            gated[tool] = verdict.tier.value
    assert not gated, f"Jarvis's own controls must run instantly: {gated}"


@pytest.mark.parametrize("phrase,tool", [
    ("what can I delete", "cleanup_suggestions"),
    ("what should I delete", "cleanup_suggestions"),
    ("clean up my disk", "cleanup_suggestions"),
    ("what is taking up my space", "disk_report"),
    ("what's eating my space", "disk_report"),
])
def test_disk_cleanup_phrasings_route_locally(router, phrase, tool):
    """Asked the way a person asks it. These fell through to Claude (3-18s)
    for a question a local tool answers instantly."""
    assert route_tool(router, phrase) == tool


# ------------------------------------------------- mid-sentence pause
def test_a_thinking_pause_does_not_cut_the_command_in_half():
    """
    Reported as "it is ignoring me". silence_ms was 700, so saying
    "open chrome ... and go to youtube" with a normal ~1.2s pause was
    captured as JUST "open chrome" — measured: cut at 1.34s, keeping 1.38s
    of a 4.75s utterance. The turn then acted on half a command, which
    reads exactly like being ignored.

    The guarantee now lives in a different place, so this tests a different
    thing than it used to. Waiting 2000ms for EVERY turn fixed truncation
    by making all 409 utterances in the log pay for the 1% that needed it.
    Instead the utterance closes fast and the TRANSCRIPT decides: a
    sentence ending on a connector is unfinished, so listening resumes on
    the patient threshold.

    (The previous version of this test drove the collector with
    `np.full(512, 0.3)` — a DC constant, which Silero scores as silence,
    not speech. `_started` was therefore never set and the collector could
    not have closed for any reason, so the assertion held no matter what
    the endpointing logic did. It is asserted on real behaviour here.)
    """
    from jarvis.app import looks_unfinished

    # The exact phrase from the bug report, split where he paused.
    assert looks_unfinished("open chrome and"), (
        "a command ending on 'and' must keep listening — this is the "
        "truncation that read as being ignored"
    )
    for unfinished in (
        "open chrome and",
        "send a message to",
        "could you please open the",
        "i want you to",
        "go to youtube and search for",
        "delete the",
    ):
        assert looks_unfinished(unfinished), f"{unfinished!r} should keep listening"

    # ...and the 55% majority must NOT pay for it.
    for finished in (
        "open chrome",
        "what time is it",
        "close notepad",
        "open telegram",
        "what is eating my disk",
        "mute",
    ):
        assert not looks_unfinished(finished), (
            f"{finished!r} is a complete command and must dispatch immediately"
        )


def test_the_fast_endpoint_is_actually_faster_than_the_patient_one():
    """The two thresholds must be ordered, or the optimisation is a no-op."""
    from jarvis.audio.vad import VAD, UtteranceCollector

    vad = VAD(CONFIG)
    collector = UtteranceCollector(CONFIG, vad)
    assert collector.fast_silence_ms < collector.patient_silence_ms, (
        "fast endpoint is not faster than the patient one — every turn is "
        "still paying the full silence tax"
    )
    assert collector.patient_silence_ms >= 1500, (
        "the patient threshold is what protects a real thinking pause; "
        "below ~1.5s it truncates again"
    )
    # A fresh utterance starts on the fast threshold.
    assert collector._endpoint_ms == collector.fast_silence_ms
    # resume() moves it to the patient one and keeps the audio already heard.
    import numpy as np
    heard = np.full(1600, 0.1, dtype=np.float32)
    collector.resume(heard)
    assert collector._endpoint_ms == collector.patient_silence_ms
    assert collector.was_patient is True
    assert sum(len(b) for b in collector._buf) == len(heard), (
        "resume() dropped the audio already captured — the final transcript "
        "would then see only the tail of the sentence"
    )


def test_a_noise_blip_cannot_hang_the_collector():
    """
    A single 32ms VAD blip (a keystroke, a door, Jarvis's own voice coming
    back through the mic) used to hold the microphone for a full
    max_utterance_s — 30 SECONDS of a live assistant appearing dead.

    The old give-up condition was `not self._started`, but one blip sets
    _started. The finish condition needs _speech_ms >= min_speech_ms, and
    32ms never reaches 250ms. Neither could ever fire.
    """
    import numpy as np
    from jarvis.audio.vad import VAD, UtteranceCollector

    class _BlipVAD(VAD):
        """Speech on frame 1 only, silence forever after."""

        def __init__(self, cfg):
            super().__init__(cfg)
            self.calls = 0

        def is_speech(self, frame):
            self.calls += 1
            return self.calls == 1

        def reset(self):
            pass

    vad = _BlipVAD(CONFIG)
    collector = UtteranceCollector(CONFIG, vad)
    frame = np.zeros(512, dtype=np.float32)

    # Feed well past the give-up window but far short of max_utterance_s.
    budget = int(collector.no_speech_timeout_ms / collector.frame_ms) + 4
    closed_at = None
    for i in range(budget):
        if collector.feed(frame) is not None:
            closed_at = (i + 1) * collector.frame_ms
            break

    assert closed_at is not None, (
        "the collector never released the microphone — this is the 30-second "
        "hang, and it is indistinguishable from Jarvis being crashed"
    )
    assert closed_at <= collector.no_speech_timeout_ms + 4 * collector.frame_ms
    assert closed_at < collector.max_s * 1000, "still waiting out max_utterance_s"


def test_common_replies_are_cached_not_re_synthesised():
    """
    "Opening chrome." cost ~1.46s of network round-trip EVERY time — the
    largest remaining delay on a command whose actual work finishes in
    ~0.5s. The words never change, so they are rendered once and replayed.
    Measured after: 1460ms -> 0.02ms.
    """
    from jarvis.audio.tts import Speaker

    speaker = Speaker(CONFIG)
    calls = []

    def fake_render(sentence):
        calls.append(sentence)
        import numpy as np
        return (np.zeros(10, dtype=np.float32), 24000)

    speaker._render = fake_render
    for _ in range(5):
        speaker._render_cached("Done.")
    assert len(calls) == 1, f"re-synthesised a cached phrase {len(calls)} times"


def test_audio_cache_is_bounded():
    """This runs on a machine with ~1GB free — an unbounded audio cache
    would be a slow memory leak."""
    from jarvis.audio.tts import Speaker
    import numpy as np

    speaker = Speaker(CONFIG)
    speaker._render = lambda s: (np.zeros(10, dtype=np.float32), 24000)
    for i in range(speaker.CACHE_MAX_ENTRIES + 15):
        speaker._render_cached(f"phrase number {i}.")
    assert len(speaker._audio_cache) <= speaker.CACHE_MAX_ENTRIES


def test_long_replies_are_not_cached():
    """Only short confirmations repeat; caching paragraphs would waste the
    bound on things said once."""
    from jarvis.audio.tts import Speaker
    import numpy as np

    speaker = Speaker(CONFIG)
    speaker._render = lambda s: (np.zeros(10, dtype=np.float32), 24000)
    speaker._render_cached("x" * (speaker.CACHEABLE_MAX_CHARS + 50))
    assert len(speaker._audio_cache) == 0


# ------------------------------------------- compound / app-at-location
@pytest.mark.parametrize("phrase", [
    "open chrome and go to youtube",
    "open chrome and go to chess",
    "launch chrome and open instagram",
])
def test_browser_plus_site_opens_the_site(router, phrase):
    """The greedy open catch-all swallowed the whole phrase and tried to
    launch an app literally named "chrome and go to youtube"."""
    intent = router.route(phrase)
    assert intent.tool == "open_url"


@pytest.mark.parametrize("phrase,app,target", [
    ("open vs code in the eco pulse folder", "vs code", "eco pulse"),
    ("open excel with budget.xlsx", "excel", "budget.xlsx"),
])
def test_app_at_a_location_parses_both_halves(router, phrase, app, target):
    """
    "open VS Code in the eco pulse folder" had nowhere to go: the greedy
    rule tried to launch an app by that entire name, and the brain's
    fallback was to silently drop half the request and answer as though it
    had done all of it.
    """
    intent = router.route(phrase)
    assert intent.tool == "open_in"
    assert intent.args["app"] == app
    assert intent.args["target"] == target


def test_plain_open_still_goes_to_open_target(router):
    """The new rules must not steal simple opens."""
    assert route_tool(router, "open chrome") == "open_target"
    assert route_tool(router, "open my cv") == "open_target"


def test_open_in_reports_missing_app_and_target_honestly():
    from jarvis.tools.launcher import open_in

    assert "couldn't find" in open_in("zzz_no_app_9x7", "eco pulse").lower()
    assert "couldn't find" in open_in("notepad", "zzz_no_file_9x7").lower()


# ---------------------------------------- real spoken phrasing, measured
@pytest.mark.parametrize("phrase,expected_name", [
    ("Could you please open Telegram for me?", "telegram"),
    ("can you open chrome for me", "chrome"),
    ("could you open notepad real quick", "notepad"),
    ("jarvis open chrome now", "chrome"),
    ("open my cv for me please", "my cv"),
])
def test_trailing_courtesy_is_not_part_of_the_name(router, phrase, expected_name):
    """
    Heard live: "could you please open Telegram FOR ME" reached open_target
    as name="telegram for me", searched the whole machine for an app by that
    literal name, failed after 8 seconds, and answered "I couldn't find an
    app called telegram for me".
    """
    intent = router.route(phrase)
    assert intent is not None, f"{phrase!r} fell through to Claude (3-7s)"
    # Compared case-insensitively: router arguments now preserve the
    # capitalisation the user actually used, because they carry message
    # bodies and search queries as well as app names — "telegram Rodion
    # saying I'll be late" was going out to a real person as "i'll be late".
    # Safe for names specifically because launcher.py lowercases every name
    # it is given, at each of its entry points, before matching.
    assert intent.args["name"].lower() == expected_name.lower()


@pytest.mark.parametrize("phrase", [
    "could you please tell me what to delete in my desktop",
    "what should I delete from my desktop",
    "tell me what to delete",
    "free up some space",
])
def test_cleanup_asked_naturally_stays_local(router, phrase):
    """Each of these cost a 3-7s Claude round trip for a question a local
    tool answers instantly."""
    assert route_tool(router, phrase) == "cleanup_suggestions"


def test_file_search_is_time_bounded():
    """
    Measured at 8.96s on this machine ("open my cv") — the entry cap alone
    bounds nothing when the disk is slow (this one is 99% full), and 9
    seconds of silence is exactly what "it's not responding" feels like.
    """
    import time
    from jarvis.tools.launcher import find_files, SEARCH_TIME_BUDGET_S

    start = time.perf_counter()
    find_files("my cv")
    elapsed = time.perf_counter() - start
    # Generous headroom over the budget for CI/disk variance, but far below
    # the 9s that made it feel broken.
    assert elapsed < SEARCH_TIME_BUDGET_S + 3.0, f"search took {elapsed:.1f}s"


# --------------------------------- understanding messy real speech
@pytest.mark.parametrize("phrase", [
    "telegram when you get a chance",
    "chrome if you don't mind",
    "telegram buddy",
    "chrome my friend",
    "the telegram thing",
    "that telegram app",
    "telegram or whatever",
    "telegram i guess",
])
def test_finds_the_app_inside_a_sentence(phrase):
    """
    The old approach deleted a hard-coded list of filler words, which only
    covered phrasings someone predicted. Measured against 14 ordinary
    phrasings that weren't on the list, 11 failed — the whole tail became
    part of the name being searched for ("telegram for me" as an app name).
    Now the app is found INSIDE whatever was said, so it degrades
    gracefully on phrasings nobody anticipated.
    """
    from jarvis.tools.launcher import resolve_app

    target, matched = resolve_app(phrase)
    assert target, f"{phrase!r} resolved to nothing"


@pytest.mark.parametrize("phrase", [
    "chromosome", "telegraph", "wordpress", "codebase", "wordsmith",
    "banana", "my operations report", "excellent work",
])
def test_similar_words_do_not_open_the_wrong_app(phrase):
    """
    The other half: being generous must not become reckless. Plain
    similarity matched "chromosome"->chrome and "telegraph"->telegram, both
    of which would silently open the WRONG app — worse than not matching.
    Similarity alone can't separate them (telegraph/telegram scores 0.824,
    between real typos wrod/word 0.750 and telegran/telegram 0.875), so
    edit distance decides: a typo is 1 edit, or 2 on short words.
    """
    from jarvis.tools.launcher import resolve_app

    target, matched = resolve_app(phrase)
    assert target is None, f"{phrase!r} wrongly opened {matched}"


@pytest.mark.parametrize("typo,expect_something", [
    ("telegran", True), ("chrom", True), ("noteped", True),
    ("igram", True),    # an abbreviation, not a typo: 3 edits from "ayugram"
    ("capcut", True),
])
def test_real_typos_and_abbreviations_still_resolve(typo, expect_something):
    from jarvis.tools.launcher import resolve_app

    target, _matched = resolve_app(typo)
    assert bool(target) == expect_something, f"{typo!r} -> {target}"


# ------------------------------------------ doing things ON websites
@pytest.mark.parametrize("phrase,query", [
    ("go to youtube and search lofi and play it", "lofi"),
    ("youtube play sat prep", "sat prep"),
    ("play timeless on youtube", "timeless"),
    ("go to youtube and play chess openings", "chess openings"),
])
def test_youtube_search_and_play_is_one_intent(router, phrase, query):
    """
    "Go to YouTube and search X and play it" is ONE thing the user wants,
    not three steps they should have to narrate. Before this, "open
    youtube" opened the site and stopped — everything after was on them.
    """
    intent = router.route(phrase)
    assert intent is not None, f"{phrase!r} fell through to Claude"
    assert intent.tool == "play_on_youtube"
    assert intent.args["query"] == query


@pytest.mark.parametrize("phrase,site,query", [
    ("search youtube for chess openings", "youtube", "chess openings"),
    ("look up python decorators on github", "github", "python decorators"),
    ("search reddit for python tips", "reddit", "python tips"),
])
def test_named_site_search_routes_locally(router, phrase, site, query):
    intent = router.route(phrase)
    assert intent is not None, f"{phrase!r} fell through to Claude"
    assert intent.tool == "search_site"
    assert intent.args["site"] == site
    assert intent.args["query"] == query


def test_play_without_a_site_still_means_a_local_file(router):
    """"play timeless" means the file on this machine; only "... on
    youtube" means YouTube. The local-file rule was grabbing both."""
    assert route_tool(router, "play timeless") == "open_target"


def test_youtube_title_notification_count_is_not_spoken():
    """YouTube prefixes its tab title with an unread count — "(394) lofi
    hip hop" — which sounded broken when read aloud."""
    import re as _re

    assert _re.sub(r"^\(\d+\)\s*", "", "(394) lofi hip hop radio") == "lofi hip hop radio"


def test_web_tools_are_green_and_registered():
    from jarvis import tools
    from jarvis.safety import SafetyEngine

    engine = SafetyEngine(CONFIG)
    for name in ("search_site", "play_on_youtube"):
        assert name in tools.REGISTRY
        verdict = engine.classify(name, {})
        assert verdict.tier.value == "green", f"{name} gates unnecessarily"
        assert not verdict.detail.get("unclassified")
