"""
Reading a hundred emails in one tool call.

He asked for exactly this and hit the wall in a real session: "could you
please continue reading all of my emails? I mean, you gotta read 100 emails
at least." Jalen answered honestly — "search_email caps at 25 per call, so
getting to 100 means several calls" — and then spent five tool calls
grinding through it, one search at a time, with the turn limit closing in.

The cap is not Gmail's. `_messages` clamps maxResults to 25 because that is
a sensible size for "find me the email from Rodion". It is the wrong size
for "go through everything since August", and the fix is a different tool
rather than a looser clamp: the small one stays small, and this one pages.

WHY IT SUMMARISES INSTEAD OF RETURNING THE MAIL
-----------------------------------------------
A hundred full emails is somewhere north of two hundred thousand characters.
That does not fit in a turn, and stuffing it in would push out the rest of
the conversation. Each message becomes one line — sender, date, subject, and
enough snippet to judge it — which is what "is there an opportunity in here"
actually needs. When one looks interesting, read_email fetches it whole.

WHY IT SORTS INTO BUCKETS
-------------------------
The same real session: 25 emails came back and every one was a polite
decline, marketing, or a LinkedIn notification. He had to sit through that
being described. Sorting on the way past means the answer starts with the
two that matter instead of the twenty-three that do not — and the
uninteresting ones are still counted, never silently dropped.
"""
from __future__ import annotations

import re
from typing import Any

# Gmail allows 500 per page; 100 is the practical ceiling for one voice turn
# once each row is summarised.
PAGE_SIZE = 100
HARD_CAP = 300

SNIPPET_CHARS = 110

# What makes a message worth his attention. Deliberately generous — a false
# positive costs him one line to read, a false negative loses the
# opportunity the whole exercise is looking for.
_PROMISING = re.compile(
    r"\b(?:opportunit|position|internship|fellowship|scholarship|grant|"
    r"funding|collaborat|research|lab\b|mentor|programme|program\b|invit|"
    r"accept|offer|interview|admission|apply|application|welcome to|"
    r"congratulat|selected|shortlist)", re.I,
)

# Mail that is definitely not a reply from a person. Checked FIRST, because
# "opportunity" appears in most marketing subject lines ever written.
_NOISE = re.compile(
    r"\b(?:linkedin|newsletter|unsubscribe|no-?reply|noreply|notification|"
    r"digest|promotion|marketing|webinar|discord|reddit|medium daily|"
    r"do not reply|automated)", re.I,
)

# Bulk-mail SENDERS, checked on the address rather than the words. Added
# after a live scan put "have you met Goldy?" from a university admissions
# blast and "Job Opportunities" from a jobs board into the worth-a-look
# pile — both contain the right words and neither is a person writing to
# him. A dedicated mail subdomain is the giveaway: real replies come from
# yanshuo@nus.edu.sg, blasts come from @email.something or @apply.something.
_BULK_SENDER = re.compile(
    r"@(?:email|mail|mailer|apply|info|news|updates|marketing|e|em)\."
    r"|\b(?:mailchimp|sendgrid|hubspot|salesforce|constantcontact)\b",
    re.I,
)


def _classify(sender: str, subject: str, snippet: str) -> str:
    blob = f"{sender} {subject} {snippet}"
    # Sender first: "opportunity" appears in most marketing subject lines
    # ever written, so the words cannot be trusted before the address is.
    if _BULK_SENDER.search(sender) or _NOISE.search(blob):
        return "noise"
    # A "Re:" from a real address is a PERSON ANSWERING HIM, and that is the
    # single strongest signal in the inbox — it is the entire category he
    # goes looking for. It needs no keyword: the reply from NUS said only
    # "thanks for reaching out, unfortunately my funding does not support",
    # which contains no opportunity word at all and is exactly the mail he
    # wanted found. Bulk senders never reach here, so this cannot promote
    # a "Re:" in a newsletter subject line.
    if re.match(r"\s*(?:re|fwd?)\s*:", subject, re.I):
        return "promising"
    if _PROMISING.search(blob):
        return "promising"
    return "other"


def scan_inbox(query: str = "", max_emails: int = PAGE_SIZE) -> str:
    """
    Go through a lot of mail in ONE call and sort it — GREEN, read-only.

    `query` takes Gmail's own syntax ("after:2026/08/10", "from:edu"). Empty
    scans the inbox.
    """
    from .gmail import _enabled, _header, _readable, gmail_service

    _enabled()
    wanted = max(1, min(int(max_emails), HARD_CAP))
    service = gmail_service()

    ids: list[dict] = []
    token = None
    while len(ids) < wanted:
        response = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query or "in:inbox",
                maxResults=min(PAGE_SIZE, wanted - len(ids)),
                pageToken=token,
            )
            .execute()
        )
        batch = response.get("messages") or []
        ids.extend(batch)
        token = response.get("nextPageToken")
        # No more pages, or Gmail returned nothing — either way, stop rather
        # than looping on an empty result forever.
        if not token or not batch:
            break

    if not ids:
        return f"No messages match {query or 'in:inbox'!r}."

    buckets: dict[str, list[str]] = {"promising": [], "other": [], "noise": []}
    failed = 0
    for entry in ids:
        try:
            msg = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=entry["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
        except Exception:
            failed += 1
            continue
        payload = msg.get("payload", {})
        sender = _readable(_header(payload, "From"))
        subject = _readable(_header(payload, "Subject")) or "(no subject)"
        date = _readable(_header(payload, "Date"))[:16]
        snippet = _readable(msg.get("snippet") or "")[:SNIPPET_CHARS]
        buckets[_classify(sender, subject, snippet)].append(
            f"  [{date}] {sender} — {subject}\n      {snippet}  [id: {entry['id']}]"
        )

    total = sum(len(v) for v in buckets.values())
    lines = [
        f"Scanned {total} message(s) matching {query or 'in:inbox'!r}.",
        f"{len(buckets['promising'])} worth a look, "
        f"{len(buckets['other'])} ordinary, {len(buckets['noise'])} automated.",
    ]
    if buckets["promising"]:
        lines += ["", "WORTH A LOOK:"] + buckets["promising"]
    if buckets["other"]:
        lines += ["", "EVERYTHING ELSE:"] + buckets["other"]
    if buckets["noise"]:
        # Counted, never silently dropped — but not listed in full, because
        # forty LinkedIn notifications are not information.
        senders = sorted({row.split("—")[0].split("]")[-1].strip()
                          for row in buckets["noise"]})[:8]
        lines += ["", f"AUTOMATED ({len(buckets['noise'])}), from: " + ", ".join(senders)]
    if failed:
        lines.append(
            f"\nCOULD NOT READ {failed} of them — so this is not the whole picture."
        )
    lines.append(
        "\nThe sorting is a guess from sender and subject. Read anything that "
        "looks close with read_email before telling him it's an opportunity."
    )
    return "\n".join(lines)


REGISTRY: dict[str, Any] = {
    "scan_inbox": scan_inbox,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
