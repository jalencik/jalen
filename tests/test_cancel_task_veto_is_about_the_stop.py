"""
"Stop the coding job, I don't need it anymore" left the job running.

cancel_task treated the words "don't" / "do not" ANYWHERE in the sentence as
a veto, so a sentence that clearly asks for the stop - "cancel the coding job
now, don't let it keep running" - was answered with "I haven't stopped
anything - that sounded like you want it left running". An independent review
of 6d34421 reproduced three such sentences. It matters most at exactly the
moment an agent is doing something he does not want.

The veto now applies only when the negation directly governs the stop verb:
"don't stop...", "do not cancel...", "never kill...", with a filler word or
two allowed ("don't you stop", "please don't ever cancel").
"""
from __future__ import annotations

import pytest

from test_coding_job_process import (  # noqa: F401 - fixtures and helpers
    WINDOWS_ONLY, hold, no_conversation_tasks, table,
)


@WINDOWS_ONLY
@pytest.mark.parametrize("said", [
    "stop the coding job, I don't need it anymore",
    "cancel the coding job now, don't let it keep running",
    "I don't want the coding job, stop it",
    "kill the background job - I don't care what it was doing",
    "stop the coding job, I do not need it",
])
def test_a_stop_with_a_dont_somewhere_else_still_stops(table, tmp_path, no_conversation_tasks, said):
    from jalen.tools import tasks

    proc = hold(table, tmp_path, "c0ffee22", "proj")
    reply = tasks.cancel_task(said)
    assert "haven't stopped" not in reply, f"{said!r} was read as a veto: {reply}"
    assert proc.poll() is not None or reply.lower().startswith("stopped"), reply


@WINDOWS_ONLY
@pytest.mark.parametrize("said", [
    "don't stop the coding job",
    "do not cancel the background job",
    "please don't stop the coding job yet",
    "don't you cancel the coding job",
    "never kill the coding job",
    "don’t stop the coding job",
])
def test_a_real_veto_still_leaves_it_running(table, tmp_path, no_conversation_tasks, said):
    from jalen.tools import tasks

    proc = hold(table, tmp_path, "c0ffee33", "proj")
    reply = tasks.cancel_task(said)
    assert proc.poll() is None, f"{said!r} stopped it: {reply}"
    assert "haven't stopped" in reply
