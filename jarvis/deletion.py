"""
The gate every deletion passes through: resolve the exact set, ask about it, delete exactly it.

THE OWNER'S RULE (2026-10-04): deleting files or folders, emptying the
recycle bin, clearing caches and any other removal of his data asks first,
and the question says WHAT will be deleted. One yes may cover a clearly
defined batch. If the set changes after the yes, the yes no longer applies.

The contract the hook relies on (jarvis/brain/agent.py):

    allowed, why_not = await deletion.gate(tool, args, confirm, summary)

`confirm` is app.confirm: it takes a jarvis.confirmation.Confirmation, speaks
its question, and returns a ConfirmAnswer (truthy only on a real yes).
When `allowed` is False, `why_not` is the sentence the model is given instead
of the tool's result, and nothing has been deleted.

THIS FIRST VERSION is the interface only: it asks about the summary. The
deletion lane replaces the body with resolve-the-set / bind / delete-that-set.
"""
from __future__ import annotations

from .confirmation import DELETE, Confirmation


async def gate(tool: str, args: dict, confirm, summary: str) -> tuple[bool, str]:
    question = f"{summary}. Confirm?"
    answer = await confirm(Confirmation(question=question, kind=DELETE,
                                        action=tool, target=summary))
    if answer:
        return True, ""
    outcome = getattr(answer, "outcome", "no")
    if outcome == "timeout":
        return False, ("He did not answer in time, so nothing was deleted. "
                       "Tell him; do not retry unless he asks.")
    if outcome == "correction":
        return False, (f"He did not approve it as asked; he said: "
                       f"{getattr(answer, 'words', '')!r}. Nothing was deleted.")
    if outcome == "unspoken":
        return False, ("The confirmation question could not be played, so nothing "
                       "was deleted. Tell him and ask him to say it again.")
    return False, "He said no. Nothing was deleted. Don't retry."
