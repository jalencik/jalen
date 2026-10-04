"""
Live QA of the real Jalen in typed mode, 2026-10-01, 55 requests. Six of the
failures were the router answering a question it should have left alone, or
sending a fair question to a tool that cannot answer it:

    "find me recent arxiv papers on speculative decoding"
        -> search_files("me recent arxiv papers on speculative decoding"),
           spoken "No files matching 'me recent arxiv ...' found."
    "search for papers about diffusion transformers"
        -> search_files, same answer. For a machine-learning engineer this is
           the most natural request there is.
    "search the web for state space models versus transformers ..."
        -> a Google tab opened and the whole address read aloud.
    "how much free disk space do I have"   -> get_system_status, which never
           says how much is free ("CPU 55 percent, ... disk 95 percent full").
    "what's eating my memory"              -> get_system_status, no program
           named, although memory_report exists and answers it.
    "close the calculator"                 -> close_app("the calculator"):
           "I can't find a window called the calculator", with the calculator
           open. "close calculator" worked.

Research goes to the brain (None here means "no rule claimed it"); file search
stays for phrasings that name a file, a folder or something of his.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jalen.brain.router import IntentRouter  # noqa: E402
from jalen.config import CONFIG  # noqa: E402


@pytest.fixture
def router():
    r = IntentRouter(CONFIG)
    r.log_misses = False
    return r


# --------------------------------------------------- research is the brain's
RESEARCH = [
    # the two from the QA run, word for word
    "find me recent arxiv papers on speculative decoding",
    "search for papers about diffusion transformers",
    # the owner's other natural phrasings
    "find papers on mixture of experts",
    "find me papers about retrieval augmented generation",
    "search for articles about prompt injection",
    "find recent research on test time compute",
    "find the latest news about speculative decoding",
    "find me a good tutorial on flash attention",
    "find me some blog posts about kv cache quantisation",
    "find me information about vector databases",
    "search for the latest preprints on state space models",
    "find a paper on rotary embeddings",
    "find me the best survey of diffusion models",
    # a research head whose topic happens to contain a file word
    "find papers about document layout analysis",
    "find recent research on file system benchmarks",
    # a long sentence is an instruction, not a file name
    "search for the best open source speech to text models",
    "find the cheapest flights from tashkent to london in november",
    # nothing to search for
    "find me",
]


@pytest.mark.parametrize("phrase", RESEARCH)
def test_a_research_phrasing_is_not_a_file_search(router, phrase):
    hit = router.route(phrase)
    assert hit is None, (
        f"{phrase!r} was routed to {hit.tool}({hit.args}) - it would search "
        "his disk for something that is on the web"
    )


# "search the web for X" opened a Google tab and read the address out. The
# brain has web_search, which reads pages and answers.
@pytest.mark.parametrize("phrase", [
    "search the web for state space models versus transformers for long sequences",
    "search the web for python tutorials",
    "search the web for silero vad",
])
def test_search_the_web_is_answered_not_opened(router, phrase):
    hit = router.route(phrase)
    assert hit is None, f"{phrase!r} -> {hit.tool}: a page opened instead of an answer"


# --------------------------------------------------- file search stays
FILES = [
    ("find my CV", "CV"),
    ("find me my resume", "resume"),
    ("where is changes.pdf", "changes.pdf"),
    ("search for eco pulse", "eco pulse"),
    ("search for my resume", "resume"),
    ("locate the SAT TOP folder", "SAT TOP"),
    ("find jalen", "jalen"),
    ("find my flying car", "flying car"),
    # "called" / "named" belong to the sentence, not to the file name
    ("find the file called budget", "budget"),
    ("find the document named invoice march", "invoice march"),
    ("find me the file called budget", "budget"),
    ("find the folder named eco pulse", "eco pulse"),
    # his own paper is a file; somebody else's paper is research
    ("find my paper draft", "paper draft"),
    ("find the paper called attention is all you need", "attention is all you need"),
    ("find papers.docx", "papers.docx"),
    ("find the papers folder", "papers"),
    ("search for notes.txt", "notes.txt"),
    # a research word with a place on his machine is still a file search
    ("find papers in my downloads", "papers in my downloads"),
]


@pytest.mark.parametrize("phrase, query", FILES)
def test_file_search_keeps_the_phrasings_that_name_a_file(router, phrase, query):
    hit = router.route(phrase)
    assert hit is not None and hit.tool == "search_files", f"{phrase!r} -> {hit}"
    assert hit.args["query"] == query


def test_find_out_about_still_goes_to_the_brain(router):
    assert router.route("find out about the horizon program deadline") is None


def test_find_files_about_is_still_a_content_search(router):
    hit = router.route("find files about eco pulse")
    assert hit.tool == "search_in_files" and hit.args["query"] == "eco pulse"


def test_where_is_and_locate_are_never_taken_for_research(router):
    """Only find / search for can be a research verb. "where is" and "locate"
    are about a place on his machine whatever the nouns are."""
    assert router.route("where is the research paper").tool == "search_files"
    assert router.route("locate the news folder").tool == "search_files"


# --------------------------------------------------- google X still opens it
@pytest.mark.parametrize("phrase", ["google python tutorials", "google c++ vector erase"])
def test_google_x_still_puts_the_results_on_screen(router, phrase):
    """He said "google", so a results page is what he asked for."""
    hit = router.route(phrase)
    assert hit.tool == "open_url"
    assert hit.args["url"].startswith("https://www.google.com/search?q=")


# --------------------------------------------------- disk and memory
@pytest.mark.parametrize("phrase", [
    "how much free disk space do I have",
    "how much space do i have",
    "how much disk space do i have",
    "how much storage do i have",
    "how much space is left",
    "how much free space is left",
])
def test_free_space_questions_go_to_the_disk_report(router, phrase):
    """get_system_status says "disk 95 percent full" and never how much is
    free; disk_report names each drive's free space."""
    assert router.route(phrase).tool == "disk_report"


@pytest.mark.parametrize("phrase", [
    "what's eating my memory",
    "what is eating my memory",
    "what's using my memory",
    "what's using up all my ram",
    "what's taking up my memory",
    "which programs are using the most memory",
    "which apps are eating my ram",
])
def test_memory_hog_questions_go_to_the_memory_report(router, phrase):
    """memory_report names the programs; get_system_status gives a percentage
    and no names."""
    assert router.route(phrase).tool == "memory_report"


@pytest.mark.parametrize("phrase", ["how much ram", "memory", "disk usage", "cpu"])
def test_the_one_line_status_questions_still_get_the_one_line_status(router, phrase):
    assert router.route(phrase).tool == "get_system_status"


# --------------------------------------------------- close the calculator
@pytest.mark.parametrize("phrase, name", [
    ("close the calculator", "calculator"),
    ("close calculator", "calculator"),
    # "my browser" was here until "browser" became a generic noun
    # (test_close_commands_never_name_a_generic_noun): close_app matches window
    # TITLES and no title says "browser", so it could only answer "I can't find
    # a window called browser". Same article, a real name:
    ("close my chrome", "chrome"),
    ("quit the notepad", "notepad"),
    ("close the calculator app", "calculator"),
    ("exit a calculator", "calculator"),
    ("close the notepad program", "notepad"),
])
def test_close_names_the_app_without_the_article(router, phrase, name):
    hit = router.route(phrase)
    assert hit.tool == "close_app" and hit.args["name"] == name


@pytest.mark.parametrize("phrase, name", [
    ("switch to the calculator", "calculator"),
    ("bring up the calculator", "calculator"),
    ("focus my chrome", "chrome"),   # was "my browser": now a generic noun, see above
    ("switch to chrome", "chrome"),
])
def test_switch_names_the_window_without_the_article(router, phrase, name):
    """Same cause as "close the calculator": the rule kept the word "the"."""
    hit = router.route(phrase)
    assert hit.tool == "focus_window" and hit.args["name"] == name


@pytest.mark.parametrize("phrase, tool", [
    ("close the window", "keyboard_shortcut"),
    ("close this", "keyboard_shortcut"),
    ("close the tab", "keyboard_shortcut"),
    ("close the youtube window", "close_browser_tab"),
    ("switch to the last window", "keyboard_shortcut"),
])
def test_the_rules_above_the_catch_all_are_untouched(router, phrase, tool):
    assert router.route(phrase).tool == tool


def test_close_with_nothing_to_close_is_not_a_window_called_the(router):
    """"close the" is not a name; it must not reach close_app at all."""
    hit = router.route("close the")
    assert hit is None or hit.tool != "close_app"
