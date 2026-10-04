"""
The gate every outward Telegram action passes through: resolve once, ask, send to what was asked about.

THE OWNER'S RULE (2026-10-04): every Telegram write that reaches anyone -
a message, a channel post, a group message, a reply, a sticker, a voice
message, a file, an edit, a reaction, a remote delete - needs his explicit
yes, his own channel and Saved Messages included. It replaces the earlier
"read the first line aloud and post unless he says stop" for channel posts.

The contract the hook relies on (jarvis/brain/agent.py):

    allowed, why_not = await outbound.gate(tool, args, confirm, summary)

`confirm` is app.confirm: it takes a jarvis.confirmation.Confirmation, speaks
its question, and returns a ConfirmAnswer (truthy only on a real yes).
`summary` is SafetyEngine's one-line description of the call. When
`allowed` is False, `why_not` is the sentence the model is given instead of
the tool's result - a refusal, a timeout, or a question back to him ("two
chats match 'Ali' ...") - and nothing has been sent.

THIS FIRST VERSION is the interface only: it asks about the summary. The
Telegram lane replaces the body with resolve-once / freeze / send-to-frozen.
"""
from __future__ import annotations

from .confirmation import TELEGRAM, Confirmation


async def gate(tool: str, args: dict, confirm, summary: str) -> tuple[bool, str]:
    question = f"{summary}. Confirm?"
    answer = await confirm(Confirmation(question=question, kind=TELEGRAM,
                                        action=tool, target=summary))
    if answer:
        return True, ""
    return False, _deny_reason(answer)


def _deny_reason(answer) -> str:
    outcome = getattr(answer, "outcome", "no")
    if outcome == "timeout":
        return ("He did not answer the confirmation in time, so nothing was sent. "
                "Tell him it was not sent; do not retry unless he asks.")
    if outcome == "correction":
        return (f"He did not approve it as asked; he said: {getattr(answer, 'words', '')!r}. "
                "Nothing was sent. Act on what he said instead.")
    if outcome == "unspoken":
        return ("The confirmation question could not be played, so nothing was sent. "
                "Tell him in one sentence and ask him to say it again.")
    return "He said no. Nothing was sent. Don't retry."
