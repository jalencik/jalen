"""
Packaging, licence and the update path — HANDOFF item 6.

Small file, but each assertion stands over a specific way this goes wrong
quietly:

  * two dependency lists that drift produce an install which imports fine
    and fails at the first real call
  * a console entry point naming a function that does not exist fails only
    when someone types the command, which is months later
  * an update script that loses a file loses a vault nobody can recreate
"""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_the_entry_points_name_functions_that_exist():
    """
    A console script pointing at a missing function fails at the moment
    someone types the command, with an import error rather than anything
    useful. Cheap to check now, invisible otherwise.
    """
    import importlib

    for command, target in pyproject()["project"]["scripts"].items():
        module_name, _, function = target.partition(":")
        module = importlib.import_module(module_name)
        assert hasattr(module, function), f"{command} points at missing {target}"
        assert callable(getattr(module, function))


def test_dependencies_come_from_the_one_list():
    """
    requirements.txt is the source of truth and pyproject reads it. A second
    hand-maintained copy drifts, and the failure mode is an install that
    imports and then dies at the first real call.
    """
    data = pyproject()
    assert "dependencies" in data["project"]["dynamic"]
    assert data["tool"]["setuptools"]["dynamic"]["dependencies"]["file"] == [
        "requirements.txt"
    ]


def test_the_camera_dependency_is_gone_entirely():
    """
    It used to be optional. Now there is no camera code to depend on it, so
    "optional" would just be ~250 MB nobody can ever use.
    """
    core = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "mediapipe" not in core
    assert "opencv" not in core


def test_the_gesture_extra_is_gone_along_with_the_feature():
    """
    Hand-gesture resizing was removed at his explicit request. A leftover
    `gestures` extra would still pull ~250 MB of mediapipe and opencv for
    code that no longer exists — the "half-dead infrastructure" he asked
    specifically not to be left with.
    """
    extras = pyproject()["project"].get("optional-dependencies", {})
    assert "gestures" not in extras
    assert not (ROOT / "requirements-gestures.txt").exists()
    assert not (ROOT / "jarvis/ui/gestures.py").exists()
    for name in ("mediapipe", "opencv"):
        assert name not in (ROOT / "requirements.txt").read_text(encoding="utf-8")


def test_the_python_floor_matches_what_the_code_needs():
    """
    The codebase uses `X | None` annotations at runtime in dataclasses and
    tomllib, so the floor is not decorative.
    """
    assert pyproject()["project"]["requires-python"] == ">=3.12"


def test_there_is_a_licence_and_pyproject_points_at_it():
    data = pyproject()
    assert data["project"]["license"]["file"] == "LICENSE"
    body = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert body.strip()
    # It says how to change its mind, because the choice is asymmetric and
    # was made on his behalf.
    assert "MIT" in body, "the licence does not explain how to open-source it"
    assert "third-party" in body.lower(), (
        "nothing warns that the dependencies and models carry their own terms"
    )


def test_the_update_script_backs_up_everything_irreplaceable():
    """
    The list is the whole safety property. A file missing from it is a file
    an update can destroy — and data/vault.json genuinely cannot be
    recovered, because the passphrase is stored nowhere by design.
    """
    from scripts.update import PRECIOUS

    for irreplaceable in (
        ".env",
        "config/user.yaml",
        "data/vault.json",
        "data/google_token.json",
        "data/telegram_user.session",
        "models/hey_jalen.onnx",
    ):
        assert irreplaceable in PRECIOUS, f"{irreplaceable} would be lost by an update"


def test_the_update_script_refuses_rather_than_stashing():
    """
    A stash is where uncommitted work goes to be forgotten. An update script
    whose purpose is not losing things must not create one.
    """
    import inspect

    from scripts import update

    source = inspect.getsource(update)
    # The word appears in the comment explaining the choice, so the check is
    # on the call.
    assert 'git("stash"' not in source
    assert '"stash"' not in source.replace("# ", "")


def test_the_update_script_does_not_roll_back_by_itself():
    """
    It prints the command instead. An automatic rollback that goes wrong
    leaves a state nobody chose, on a machine whose owner is not watching.
    """
    import inspect

    from scripts import update

    source = inspect.getsource(update)
    assert 'git("reset"' not in source
    assert "git reset --hard" in source, "it does not tell him how to go back"
