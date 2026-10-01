"""
The never-touch list protected one spelling of each path.

safety.py compared the RAW argument against the list, while every tool that
touches a file expands it first - filesystem._resolve is
Path(os.path.expandvars(os.path.expanduser(path))). So the gate and the tool
disagreed about which file was being touched. Reproduced against the real
engine with read_file:

    C:\\Users\\user\\.ssh\\config                         BLACK
    ~/.ssh/config                                          GREEN
    %USERPROFILE%/.ssh/config                              GREEN
    C:\\Users\\user\\Desktop\\x\\..\\credentials\\notes.txt  GREEN
    C:\\Temp\\..\\Windows\\System32                          GREEN
    \\\\?\\C:\\Windows\\notepad.exe  (long-path form)        GREEN
    folder="~/.ssh"          (argument name not inspected)  GREEN
    target="~/Desktop/credentials/list.txt"                 GREEN

The last two are the second half of the bug: only argument NAMES containing
path, file or dir were inspected at all, so project_status(folder=...) and
open_in(target=...) walked straight past it - with origin=content too.

THE RULE NOW: canonicalise a path the way the tools do - variables, ~, "..",
the long-path prefix - before comparing, and inspect any argument whose name
suggests a location or whose whole value looks like a path. Pure string
operations: nothing touches the filesystem, and anything that cannot be
canonicalised is refused rather than waved through.

WHAT IT MUST NOT DO: refuse prose. An email body or a message that merely
MENTIONS C:/Windows is not a request to touch it, and a guard that fires on
every sentence gets switched off.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis.config import CONFIG
from jarvis.safety import SafetyEngine, Tier

HOME = str(Path.home())


@pytest.fixture
def engine():
    return SafetyEngine(CONFIG)


@pytest.mark.parametrize("args", [
    {"path": "~/.ssh/config"},
    {"path": "%USERPROFILE%/.ssh/config"},
    {"path": "%USERPROFILE%\\.ssh\\id_ed25519.pub"},
    {"path": HOME + "\\Desktop\\x\\..\\credentials\\notes.txt"},
    {"path": "C:\\Temp\\..\\Windows\\System32"},
    {"path": "c:/windows/system32/drivers/etc/hosts"},
    {"path": "\\\\?\\C:\\Windows\\notepad.exe"},
    {"folder": "~/.ssh"},
    {"target": "~/Desktop/credentials/list.txt"},
    {"source": "~/.aws/credentials"},
    {"destination": "~/.ssh"},
    {"where": "%USERPROFILE%\\.gnupg"},
])
def test_every_spelling_of_a_protected_path_is_refused(engine, args):
    verdict = engine.classify("read_file", args)
    assert verdict.tier is Tier.BLACK, f"{args} reached a never-touch path"


@pytest.mark.parametrize("args", [
    {"path": HOME + "\\Desktop\\report.pdf"},
    {"path": "~/Desktop/notes.txt"},
    {"path": "%USERPROFILE%\\Documents\\cv.docx"},
    {"folder": "~/Desktop/projects/eco-pulse"},
])
def test_ordinary_files_are_still_allowed(engine, args):
    assert engine.classify("read_file", args).tier is not Tier.BLACK, args


@pytest.mark.parametrize("args", [
    {"text": "the installer lives in C:/Windows/System32, apparently"},
    {"body": "Your ~/.ssh keys are safe, we promise."},
    {"message": "please don't touch %USERPROFILE%\\.ssh"},
])
def test_prose_that_mentions_a_path_is_not_a_path(engine, args):
    """A sentence ABOUT C:/Windows is not a request to open it."""
    assert engine.classify("send_telegram_message", {"to": "Saved Messages", **args}
                           ).tier is not Tier.BLACK, args


def test_a_protected_filename_is_caught_in_any_folder_spelling(engine):
    """Patterns (.env, *.session, id_rsa*) are matched after expansion too."""
    for raw in ("~/projects/app/.env", "%USERPROFILE%/Desktop/x/../telegram_user.session",
                "~/.ssh/../keys/id_rsa"):
        assert engine.classify("read_file", {"path": raw}).tier is Tier.BLACK, raw


@pytest.mark.parametrize("tool,args", [
    # Copying INTO ~/.ssh is how a key gets planted; destination= was never
    # inspected.
    ("copy_file", {"path": "~/Downloads/key.pub", "destination": "~/.ssh/authorized_keys"}),
    ("move_file", {"path": "~/Downloads/x.txt", "destination": "%USERPROFILE%\\.aws"}),
    # open_url hands a file: URL straight to os.startfile.
    ("open_url", {"url": "file:///C:/Users/" + Path.home().name + "/.ssh/config"}),
    ("open_url", {"url": "FILE:///c:/windows/system32/cmd.exe"}),
    ("open_url", {"url": "file:///C:/Users/x/Desktop/My%20Passwords.txt"}),
    # A protected NAME used as a folder protects what is inside it.
    ("read_file", {"path": "D:/backup/Mother credentials/bank.txt"}),
    ("read_file", {"path": "~/Documents/passwords/gmail.txt"}),
])
def test_the_ways_around_it_that_the_old_check_never_looked_at(engine, tool, args):
    assert engine.classify(tool, args).tier is Tier.BLACK, (tool, args)


@pytest.mark.parametrize("tool,args", [
    # A place, not a file.
    ("create_calendar_event", {"title": "ML meetup", "location": "Password workshop, Room 3"}),
    # A web page, not a file, even in a path-ish argument.
    ("remember_alias", {"alias": "passwords", "target": "https://passwords.google.com"}),
    ("open_url", {"url": "https://myaccount.google.com/security/credentials"}),
])
def test_things_that_only_sound_like_protected_files_are_not_refused(engine, tool, args):
    assert engine.classify(tool, args).tier is not Tier.BLACK, (tool, args)


def test_the_working_directory_is_not_mistaken_for_what_he_wrote(engine, monkeypatch, tmp_path):
    """
    A relative path is resolved against the working directory for the
    PREFIX check - that is what the tool opens - but the patterns only look
    at what he wrote. Started from a folder called credentials-app, every
    relative path would otherwise be refused.
    """
    start = tmp_path / "credentials-app"
    start.mkdir()
    monkeypatch.chdir(start)
    assert engine.classify("read_file", {"path": "notes/todo.txt"}).tier is not Tier.BLACK
    assert engine.classify("read_file", {"path": "notes/../.env"}).tier is Tier.BLACK


def test_a_path_that_cannot_be_canonicalised_is_refused_not_waved_through(engine, monkeypatch):
    import jarvis.safety as safety

    def boom(p):
        raise ValueError("cannot canonicalise")

    monkeypatch.setattr(safety, "_canonical_path", boom)
    assert engine.classify("read_file", {"path": "~/anything"}).tier is Tier.BLACK


def test_the_check_still_runs_before_the_tier_lookup(engine):
    """
    A GREEN tool must not be a way around it: never-touch is step 1 of
    classify, above the tier table (safety.py's own comment).
    """
    import inspect

    source = inspect.getsource(SafetyEngine.classify)
    assert source.index("_touches_forbidden_path") < source.index('origin == "content"')


# ---------------------------------------------------------------------------
# A path inside a spoken NAME (R5-1 of the collaborator's adversarial review)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tool, name", [
    ("open_target", "file C:\\Windows\\System32\\cmd.exe"),
    ("open_target", "the folder ~\\.ssh"),
    ("open_target", "document named %USERPROFILE%\\.aws\\credentials"),
    ("open_app", "please C:/Windows/System32/cmd.exe"),
])
def test_a_protected_path_inside_a_spoken_name_is_refused(engine, tool, name):
    assert engine.classify(tool, {"name": name}).tier is Tier.BLACK, (tool, name)


@pytest.mark.parametrize("tool, name", [
    ("open_target", "spotify"),
    ("open_target", "my resume"),
    ("open_target", "notes about the C drive"),
    ("open_target", "file C:\\Users\\someone\\Desktop\\report.pdf"),
    ("remember_alias", "work folder"),
])
def test_an_ordinary_spoken_name_is_still_allowed(engine, tool, name):
    assert engine.classify(tool, {"name": name}).tier is not Tier.BLACK, (tool, name)
