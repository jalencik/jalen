"""
"Open X" misses, measured from his own audit log.

data/audit.jsonl (2026-08-19 .. 2026-08-31): 57 open_* actions, 16 of them
answered with "I couldn't find an app, file or folder called ..." or a
"which one?" list - 28%. Replaying each of the 16 arguments through TODAY's
open_target (launching stubbed) leaves these causes standing:

  1. FILLER FRAGMENTS ARE SEARCHED AS FILE NAMES. "Can you open it up?"
     reached open_target as name="it up". find_files matches each word as a
     SUBSTRING and every word has to match, so "it" and "up" were found
     inside "pre_dedup_splits" ("spl-IT-s", "ded-UP") and the reply was
     "Opened pre_dedup_splits.json" - a random file on his Desktop, opened.
     The worst kind of miss: not "I couldn't", but the wrong thing.
  2. THE WORDS HE USED TO DESCRIBE THE THING ARE SEARCHED AS ITS NAME.
     "open the pdf name, the art of programs" (4 lines in
     data/router_misses.log - 7 words, so the router refused it and it cost a
     brain round trip), "my quote called that", "the document called
     changes": "pdf", "name", "called" are looked for in the file name. And
     "my cv" - the stem of a file called MY CV.pdf - was stripped to "cv",
     matched 12 files, and asked "which one?" about a file whose name is
     exactly what he said.

Plus one found by probing for the disk work in this change: "open the D
drive" resolved to nothing at all.

Nothing here launches anything. _startfile and subprocess.Popen are recorded,
and the search roots are tmp_path.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.tools import launcher  # noqa: E402


@pytest.fixture
def desk(tmp_path, monkeypatch):
    """A Desktop of tmp files, with every launch recorded instead of performed."""
    root = tmp_path / "Desktop"
    root.mkdir()
    launched: list[str] = []
    monkeypatch.setattr(launcher, "_search_roots", lambda: [root])
    monkeypatch.setattr(launcher, "_startfile", lambda target: launched.append(str(target)))
    monkeypatch.setattr(launcher, "_appeared", lambda *a, **k: True)
    monkeypatch.setattr(launcher, "resolve_app", lambda name: (None, None))
    monkeypatch.setattr(launcher, "_load_aliases", lambda: {})

    class Desk:
        path = root
        opened = launched

        @staticmethod
        def add(*names: str) -> None:
            for n in names:
                (root / n).write_text("x")

    return Desk


# ---------------------------------------------------------------- 1. fillers
def test_it_up_does_not_open_a_random_file(desk):
    """The real one: name='it up' opened pre_dedup_splits.json."""
    desk.add("pre_dedup_splits.json", "pre_dedup_splits.sha256")
    said = launcher.open_target("it up")
    assert desk.opened == [], f"opened {desk.opened}"
    assert "what" in said.lower()


@pytest.mark.parametrize("fragment", ["it", "that", "this one", "them", "up it", "that one again", "it up", "those"])
def test_a_phrase_made_only_of_fillers_is_asked_about_never_searched(desk, fragment):
    desk.add("split_it_up.txt", "that.txt", "them_all.pdf", "pre_dedup_splits.json")
    said = launcher.open_target(fragment)
    assert desk.opened == []
    assert "what" in said.lower() and "couldn't find" not in said.lower()


def test_filler_words_are_dropped_from_a_real_name(desk):
    desk.add("first_draft.txt", "unrelated.txt")
    launcher.open_target("that first draft again")
    assert [Path(p).name for p in desk.opened] == ["first_draft.txt"]


def test_find_files_does_not_search_for_a_filler_only_query(desk):
    desk.add("pre_dedup_splits.json", "split_it_up.txt")
    assert launcher.find_files("it up") == []
    assert launcher.find_files("my") == []


def test_a_taught_nickname_is_still_found_even_if_it_looks_like_a_filler(desk, monkeypatch):
    nickname = str(desk.path / "mine.lnk")
    monkeypatch.setattr(launcher, "_load_aliases", lambda: {"it": nickname})
    desk.add("mine.lnk")
    monkeypatch.setattr(launcher, "resolve_app", lambda name: (nickname, "it") if name == "it" else (None, None))
    launcher.open_target("it")
    assert desk.opened, "a nickname he taught himself must still win"


# ------------------------------------------------------------- 2. descriptors
def test_the_pdf_name_x_opens_the_pdf_named_x(desk):
    """data/router_misses.log: 'open the pdf name, the art of programs' x4."""
    desk.add("The Art of Programs - Opportunity Tracker.pdf", "Art of Programs notes.txt",
             "the-art-of-programs-draft.docx")
    launcher.open_target("pdf name, the art of programs")
    assert [Path(p).name for p in desk.opened] == ["The Art of Programs - Opportunity Tracker.pdf"]


def test_the_x_called_y_drops_the_describing_words(desk):
    desk.add("Changes.docx", "unrelated.txt")
    launcher.open_target("the document called changes")
    assert [Path(p).name for p in desk.opened] == ["Changes.docx"]


def test_a_kind_argument_works_the_same_as_the_kind_in_the_name(desk):
    desk.add("Budget.xlsx", "Budget notes.txt")
    launcher.open_target("budget", kind="spreadsheet")
    assert [Path(p).name for p in desk.opened] == ["Budget.xlsx"]


def test_a_kind_that_matches_nothing_does_not_hide_the_other_hits(desk):
    desk.add("Budget notes.txt")
    launcher.open_target("budget", kind="spreadsheet")
    assert [Path(p).name for p in desk.opened] == ["Budget notes.txt"]


def test_x_called_that_is_asked_about_not_searched(desk):
    """'my quote called that' (2026-08-20 19:33) was searched for as 'quote called that'."""
    desk.add("that.txt", "quote.txt")
    said = launcher.open_target("my quote called that")
    assert desk.opened == []
    assert "what" in said.lower()


def test_a_file_actually_called_name_something_is_still_found(desk):
    desk.add("Name Tag Template.pdf")
    launcher.open_target("name tag template")
    assert [Path(p).name for p in desk.opened] == ["Name Tag Template.pdf"]


def test_my_cv_opens_the_file_called_exactly_that(desk):
    """'my cv' was stripped to 'cv', matched 12 files, and asked 'which one?'."""
    desk.add("MY CV.pdf", "Real CV.pdf", "Musayev Jaloliddin CV.docx", "Jaloliddin_Musayev_CV.docx")
    said = launcher.open_target("my cv")
    assert [Path(p).name for p in desk.opened] == ["MY CV.pdf"], said


def test_a_bare_name_with_several_different_matches_still_asks(desk):
    desk.add("Real CV.pdf", "Musayev Jaloliddin CV.docx")
    said = launcher.open_target("cv")
    assert desk.opened == []
    assert "which one" in said.lower()


# ------------------------------------------------------------------ 3. drives
@pytest.mark.parametrize("spoken", ["the d drive", "d drive", "my D drive", "drive d", "d:", "the D: drive", "local disk d"])
def test_a_drive_can_be_opened_by_the_name_people_say(desk, monkeypatch, spoken):
    monkeypatch.setattr(launcher.os.path, "isdir", lambda p: str(p).upper().startswith("D:"))
    said = launcher.open_target(spoken)
    assert [p.upper() for p in desk.opened] == ["D:\\"], (spoken, desk.opened)
    assert "D" in said


def test_a_drive_that_is_not_there_is_said_not_searched_for(desk, monkeypatch):
    monkeypatch.setattr(launcher.os.path, "isdir", lambda p: False)
    said = launcher.open_target("the e drive")
    assert desk.opened == []
    assert "no E drive" in said


def test_a_lone_letter_is_not_a_drive(desk):
    desk.add("d.txt")
    launcher.open_target("d")
    assert not [p for p in desk.opened if p.upper().startswith("D:")]


# ------------------------------------------------------------ 3b. explicit paths
# Third cause, found by replaying every open_target / open_app argument in
# data/audit.jsonl through today's code with launching stubbed (37 distinct):
#
#   * an explicit path was checked for being a SENTENCE before it was checked
#     for existing. "C:\Users\user\Desktop\The Art of Programs - Jaloliddin's
#     Opportunity Tracker.pdf" is eight words, so it was answered "I'm not sure
#     what to open from..." however real the file was (2026-08-20 14:59).
#   * "the folder c:/users/user/desktop/jarvis-setup/jarvis/data" (2026-08-19,
#     twice): a real folder, searched for as a file called "folder c:/users/..."
#   * a path to a file that is no longer there fell through to the fuzzy APP
#     match on whatever word was in it. "C:\Users\user\Desktop\telegram_post_
#     boxette.txt" - which did not exist - replied "Opening ayugram." and
#     launched Telegram. The worst kind of miss: not "I couldn't", but the
#     wrong thing. (2026-08-20 19:25 and 19:30.)
needs_drive_letters = pytest.mark.skipif(os.name != "nt", reason="a drive-letter path is a Windows spelling")


@needs_drive_letters
def test_an_existing_path_with_many_words_is_opened_not_mistaken_for_a_sentence(desk):
    target = desk.path / "The Art of Programs - Jaloliddin's Opportunity Tracker v2 final.pdf"
    target.write_text("x")
    said = launcher.open_target(str(target))
    assert desk.opened == [str(target)], said
    assert "not sure" not in said.lower()


@needs_drive_letters
@pytest.mark.parametrize("lead", ["the folder ", "folder ", "the file ", "the directory ", "my folder "])
def test_a_kind_word_in_front_of_a_path_is_not_part_of_the_path(desk, lead):
    folder = desk.path / "some data folder"
    folder.mkdir()
    said = launcher.open_target(lead + str(folder).replace("\\", "/"))
    assert [Path(p) for p in desk.opened] == [folder], said
    assert "couldn't find" not in said.lower()


@needs_drive_letters
def test_a_path_to_nothing_does_not_launch_an_app_that_shares_a_word_with_it(desk, monkeypatch):
    """The real one: a missing telegram_post_boxette.txt opened the Telegram app."""
    monkeypatch.setattr(launcher, "resolve_app",
                        lambda name: ("C:/apps/AyuGram.lnk", "ayugram") if "telegram" in name.lower() else (None, None))
    said = launcher.open_target(str(desk.path / "telegram_post_boxette.txt"))
    assert desk.opened == [], f"opened {desk.opened}"
    assert "couldn't find" in said.lower() and "telegram_post_boxette.txt" in said


@needs_drive_letters
def test_a_file_that_moved_is_found_by_its_exact_name_and_he_is_told(desk):
    desk.add("Changes.pdf", "Changes notes.txt")
    gone = r"C:\Users\nobody\Desktop\Changes.pdf"
    said = launcher.open_target(gone)
    assert [Path(p).name for p in desk.opened] == ["Changes.pdf"], said
    assert "changes.pdf" in said.lower() and "found" in said.lower(), "he is told it was not where he said"


@needs_drive_letters
def test_two_files_with_that_name_are_asked_about_not_guessed(desk):
    (desk.path / "a").mkdir()
    (desk.path / "b").mkdir()
    (desk.path / "a" / "Changes.pdf").write_text("x")
    (desk.path / "b" / "Changes.pdf").write_text("x")
    said = launcher.open_target(r"C:\Users\nobody\Desktop\Changes.pdf")
    assert desk.opened == [], desk.opened
    assert "which" in said.lower()


@needs_drive_letters
def test_a_relative_looking_name_is_still_searched_the_old_way(desk):
    desk.add("budget.xlsx")
    launcher.open_target("budget")
    assert [Path(p).name for p in desk.opened] == ["budget.xlsx"]


# ------------------------------------------------------------------- 4. router
@pytest.fixture
def router():
    from jarvis.brain.router import IntentRouter
    from jarvis.config import CONFIG

    return IntentRouter(CONFIG)


def test_the_router_takes_the_pdf_name_phrasing_instead_of_paying_for_the_brain(router):
    intent = router.route("open the pdf name, the art of programs")
    assert intent is not None and intent.tool == "open_target"
    assert intent.args == {"name": "the art of programs", "kind": "pdf"}


@pytest.mark.parametrize("phrase,name,kind", [
    ("open the document called changes", "changes", "document"),
    ("pull up the spreadsheet named q3 budget", "q3 budget", "spreadsheet"),
    ("could you please open the pdf called the art of programs for me", "the art of programs", "pdf"),
])
def test_the_router_hears_a_described_file(router, phrase, name, kind):
    intent = router.route(phrase)
    assert intent is not None and intent.tool == "open_target", phrase
    assert intent.args["name"].lower() == name and intent.args["kind"].lower() == kind


def test_the_router_still_leaves_instructions_to_the_brain(router):
    for phrase in ("open the telegram and send hi to my saved messages",
                   "open the chat called saved messages in telegram and write hi",
                   "open my gmail and tell me what the professor said about the benchmark"):
        intent = router.route(phrase)
        assert intent is None or intent.args.get("kind") is None, phrase


def test_open_target_tells_the_brain_about_the_kind_argument():
    from jarvis.brain.tools import TOOL_SPECS

    _description, params = TOOL_SPECS["open_target"]
    assert "kind" in params and params["kind"][2] is False
