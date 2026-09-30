"""
search_in_files returned the contents of files the never-touch list protects.

Its walk excluded never-touch DIRECTORIES (filesystem._is_under_never_touch)
and nothing else. The filename PATTERNS - "*password*", "*credential*",
".env", "*.key" - live only in SafetyEngine, so a passwords.txt or a
credentials.json sitting in an ordinary folder was opened and the lines
around the match came back to the model. "Search my documents for api_key"
put a secret into a tool result, which is what CLAUDE.md's second invariant
exists to forbid: a tool result reaches the model, the transcript, the audit
log and possibly the speakers.

It is the same drift attachments.py's own comment warned about: "A second
implementation of 'is this protected' is how the two drift apart and one of
them stops matching." So there is one now - SafetyEngine.protected_path -
and the content walk, read_document and send_telegram_file all ask it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.tools import documents


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "todo.txt").write_text("remember the api_key rotation", encoding="utf-8")
    (tmp_path / "notes" / "passwords.txt").write_text("gmail api_key = SECRET-ONE", encoding="utf-8")
    (tmp_path / "notes" / "credentials.json").write_text('{"api_key": "SECRET-TWO"}', encoding="utf-8")
    (tmp_path / "Mother credentials").mkdir()
    (tmp_path / "Mother credentials" / "bank.txt").write_text("api_key SECRET-THREE", encoding="utf-8")
    return tmp_path


def test_a_protected_file_is_never_opened_by_a_content_search(tree):
    out = documents.search_in_files("api_key", folder=str(tree))
    for secret in ("SECRET-ONE", "SECRET-TWO", "SECRET-THREE"):
        assert secret not in out, f"{secret} reached a tool result"


def test_an_ordinary_file_is_still_found(tree):
    out = documents.search_in_files("api_key", folder=str(tree))
    assert "todo.txt" in out and "rotation" in out


def test_read_document_refuses_a_pattern_protected_file(tree):
    out = documents.read_document(str(tree / "notes" / "passwords.txt"))
    assert "SECRET-ONE" not in out
    assert "protected" in out.lower()


def test_one_implementation_is_asked_everywhere():
    import inspect

    from jarvis.tools import attachments

    assert "protected_path(" in inspect.getsource(documents.search_in_files)
    assert "protected_path(" in inspect.getsource(attachments._is_protected)


def test_the_engine_answers_the_question_directly():
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine

    engine = SafetyEngine(CONFIG)
    assert engine.protected_path(Path.home() / ".ssh" / "config")
    assert engine.protected_path("D:/backup/passwords.txt")
    assert engine.protected_path(Path.home() / "Desktop" / "report.pdf") is None
