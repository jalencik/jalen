r"""
What he actually asked for: the ACTION and the DESTINATION, held separately.

THE FAILURE THIS EXISTS TO CATCH
--------------------------------
From his log, 2026-08-23:

    He:    "could you please make a post about them and send them in my
            saved messages"
    Jalen: community_post_guide -> voice_guide -> save_telegram_draft
    He:    "Just send it to my saved messages."

He said SEND. It made a DRAFT. He had to say it twice, and the second time
he had to strip the request down to nothing to get past whatever the model
had decided he meant.

His words afterwards: *"why is it doing smth that I haven't asked... I said
it to send it to my saved messages."*

WHY A PROMPT WOULD NOT FIX THIS
-------------------------------
The obvious repair is to tell the model to obey the destination. That is
already implied by every instruction it has, and it did it anyway. Telling a
model something more firmly is not a mechanism.

So the verb and the place are pulled out of his sentence BEFORE the model
sees it, kept as a contract for the turn, and compared against what actually
ran afterwards. A mismatch is not silently corrected - correcting it
automatically would be another guess - it is REPORTED, so the wrong thing
having happened is something he is told rather than something he discovers.

The three parts are deliberately separate, because they fail separately:

    ACTION       send / draft / save / open / play / delete / read
    DESTINATION  Saved Messages / a channel / an address / a path
    CONTENT      whatever is being acted on

"Draft it to Saved Messages" and "send it to Saved Messages" share a
destination and are not the same request. "Send it to Antonis" and "send it
to Saved Messages" share an action and are not the same request either.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The verbs, and what each one is NOT allowed to become.
#
# The forbidden list is the point. "send" quietly becoming "draft" is the
# exact failure, and it is only catchable if the difference is written down
# somewhere a test can read.
ACTIONS: dict[str, tuple[str, ...]] = {
    # Tool names checked against the live registry, not guessed. The one
    # that actually caught him was save_telegram_draft, and an earlier draft
    # of this list spelled it "draft_telegram_post" - which exists, and is
    # not what ran. A forbidden list with the wrong name in it is a check
    # that passes while the bug happens.
    "send":   ("draft_email", "draft_email_with_file", "save_draft_text",
               "save_telegram_draft", "create_file", "list_drafts"),
    "draft":  ("send_email", "send_telegram_message", "send_posts",
               "send_telegram_file"),
    "save":   ("send_email", "send_telegram_message", "send_posts",
               "send_telegram_file"),
    "open":   ("web_search", "search_in_files"),
    "play":   ("open_target", "search_in_files", "web_search"),
    "delete": (),
    "read":   ("send_email", "send_telegram_message", "send_posts",
               "delete_file"),
}

_VERBS = (
    ("send", r"\b(?:send|post|share|deliver|fire (?:it )?off|shoot)\b"),
    ("draft", r"\b(?:draft|compose|prepare|write up)\b"),
    ("save", r"\b(?:save|store|keep|put (?:it )?in a file|write (?:it )?to)\b"),
    ("delete", r"\b(?:delete|remove|clear|wipe|bin)\b"),
    ("play", r"\b(?:play|put on|listen to)\b"),
    ("open", r"\b(?:open|launch|start up|bring up)\b"),
    ("read", r"\b(?:read|show me|tell me about)\b"),
)

# Named places he actually uses. Canonical form on the left so "my saved
# messages", "saved messages" and "the saved messages" are one destination
# rather than three.
_PLACES = (
    ("Saved Messages", r"\b(?:my |the )?saved messages\b"),
    ("your ML channel", r"\b(?:my |the )?(?:ml|machine learning|ai engineering)"
                        r"[\w &]*\bchannel\b"),
    ("your channel", r"\b(?:my |the )?channel\b"),
    ("a Gmail draft", r"\b(?:my |the )?(?:gmail )?drafts?\b"),
    ("a text file", r"\b(?:a |the )?(?:text|txt) file\b"),
    ("Notepad", r"\bnotepad\b"),
    ("Telegram", r"\btelegram\b"),
)

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_PATH = re.compile(r"\b[A-Za-z]:\\[^\s\"']+|\B~[\\/][^\s\"']+")


@dataclass
class Plan:
    """The contract for one turn."""

    action: str = ""
    destination: str = ""
    text: str = ""

    @property
    def specific(self) -> bool:
        """Did he name both halves? Only then is there anything to enforce."""
        return bool(self.action and self.destination)

    def describe(self) -> str:
        if not self.action:
            return ""
        return f"{self.action} -> {self.destination}" if self.destination else self.action

    def betrayed_by(self, tools_used) -> str:
        """
        Which tool contradicted him, if any. "" means nothing did.

        Only ever consulted when he named BOTH an action and a destination.
        Half a request is not a contract - "send this" with no destination is
        him trusting Jalen to pick, and holding him to a guess would be worse
        than the guess.
        """
        if not self.specific:
            return ""
        forbidden = ACTIONS.get(self.action, ())
        for tool in tools_used or ():
            if tool in forbidden:
                return tool
        return ""


def read_plan(text: str) -> Plan:
    """
    Pull the verb and the place out of what he said.

    Deliberately shallow. It is not trying to understand the request - the
    model does that. It is extracting the two things the model has been
    observed to substitute, so that a substitution can be noticed.
    """
    said = (text or "").strip()
    if not said:
        return Plan()

    low = said.lower()
    action = ""
    position = len(low) + 1
    for name, pattern in _VERBS:
        match = re.search(pattern, low)
        # The EARLIEST verb wins. "send me a draft" is a send; "draft it and
        # send it later" is a draft. Whichever he said first is the one he
        # led with, and leading with it is what makes it the request.
        if match and match.start() < position:
            action, position = name, match.start()

    destination = ""
    address = _EMAIL.search(said)
    path = _PATH.search(said)
    if address:
        destination = address.group(0)
    elif path:
        destination = path.group(0)
    else:
        best = len(low) + 1
        for canonical, pattern in _PLACES:
            match = re.search(pattern, low)
            if match and match.start() < best:
                destination, best = canonical, match.start()

    return Plan(action=action, destination=destination, text=said)


def complaint(plan: Plan, wrong_tool: str) -> str:
    """What to say when the wrong thing happened. Plain, and not an excuse."""
    return (
        f"That's not what you asked for — you said {plan.action}"
        f"{' to ' + plan.destination if plan.destination else ''}, and I used "
        f"{wrong_tool} instead. Say it again and I'll do the {plan.action}."
    )
