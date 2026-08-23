"""
Finding emails is not the same as understanding what he asked about them.

THE REAL FAILURE, 23 August, in his words:

    "I said you to sort out my emails about machine learning committee, you
     fucking son of a bitch."

And Jalen's own account of why, a minute later:

    "I sorted your Eco Pulse research outreach because that's what filled
     the inbox."

He was right, and it was this project's fault rather than the model's.
`scan_inbox` sorted every message into WORTH A LOOK / ordinary / automated,
where "worth a look" is `_PROMISING` — research|lab|collaborat|opportunit.
That is precisely his Eco Pulse research outreach. So the tool handed back a
list already sorted under a confident heading, answering a question he had
not asked, and the brain sorted what it had been given.

A tool that pre-judges relevance is answering its own question, and its
answer is the one that gets used.

THE FIX IS NOT A SPECIAL CASE FOR THAT EMAIL. `scan_inbox` now takes
`about` — what he actually asked for — and buckets against that. Without it
the generic sort remains, and the reply says out loud that it is generic.

The tests below are about the DISTINCTION he ran into: retrieval versus
interpretation.
"""
from __future__ import annotations

import pytest

from jarvis.tools import bulkmail


# The real shape of his inbox in that session: research outreach in bulk,
# with the thing he actually wanted buried in it.
INBOX = [
    ("grants@lab.edu", "Research collaboration on the PM2.5 benchmark",
     "following up on your eco pulse air quality work"),
    ("prof@uni.edu", "Regarding your fellowship application",
     "we reviewed your research lab position request"),
    ("team@ml-community.org", "ML community meetup next week",
     "the machine learning community is gathering to discuss"),
    ("noreply@linkedin.com", "Machine learning jobs picked for you",
     "your community of connections is hiring"),
    ("sat@talk.com", "Speaking slot at the machine learning community day",
     "we would love you to talk to the community"),
    ("billing@host.com", "Your invoice is ready", "monthly hosting charge"),
]


def bucket_of(sender, subject, snippet, about=""):
    """The bucket scan_inbox would put this row in, with and without `about`."""
    terms = bulkmail._terms(about)
    if not terms:
        return bulkmail._classify(sender, subject, snippet)
    if bulkmail._NOISE.search(f"{sender} {subject}"):
        return "noise"
    return "promising" if bulkmail._relevance(terms, sender, subject, snippet) else "other"


# ---------------------------------------------------------------------------
# THE EXACT INVERSION THAT CAUSED IT.
# ---------------------------------------------------------------------------
def test_the_generic_sort_surfaces_research_not_community():
    """
    Not a bug in itself — this is the generic sort doing its job. It is
    recorded here because it is the behaviour that got mistaken for an
    answer, and anyone changing it should see why it looked convincing.
    """
    research = bucket_of(*INBOX[0])
    community = bucket_of(*INBOX[2])
    assert research == "promising"
    assert community == "other", (
        "the generic sort no longer demonstrates the inversion this file "
        "exists to prevent"
    )


def test_asking_about_the_community_inverts_it_correctly():
    """The fix. His words decide the buckets."""
    about = "my machine learning community"
    assert bucket_of(*INBOX[0], about=about) == "other", "research still on top"
    assert bucket_of(*INBOX[2], about=about) == "promising"
    assert bucket_of(*INBOX[4], about=about) == "promising"


def test_asking_about_research_inverts_it_the_other_way():
    """
    The same mechanism has to work for the opposite request, or it is just a
    hard-coded preference for one topic wearing a parameter.
    """
    about = "the research lab replies"
    assert bucket_of(*INBOX[0], about=about) == "promising"
    assert bucket_of(*INBOX[2], about=about) == "other"


def test_noise_still_wins_over_relevance():
    """
    A LinkedIn digest that happens to contain his words is still a LinkedIn
    digest. Relevance must not promote automated mail.
    """
    assert bucket_of(*INBOX[3], about="machine learning community") == "noise"


def test_unrelated_mail_stays_out_of_the_way():
    assert bucket_of(*INBOX[5], about="machine learning community") == "other"


# ---------------------------------------------------------------------------
# The relevance test itself.
# ---------------------------------------------------------------------------
def test_common_words_do_not_match_everything():
    """
    Without a stopword list, "my" and "about" match every message ever sent
    and every row lands in the match bucket — which is the original bug with
    extra steps.
    """
    terms = bulkmail._terms("could you please look at my emails about the thing")
    assert "my" not in terms and "the" not in terms and "about" not in terms
    assert "emails" not in terms, "the word 'email' matches every email"


def test_short_words_are_ignored():
    assert bulkmail._terms("ML at a lab") == ["lab"]


def test_relevance_counts_rather_than_scores():
    """
    A plain count is checkable from a transcript: "it matched three of your
    four words". A weighted score is not, and nobody can debug it.
    """
    hits = bulkmail._relevance(
        ["machine", "learning", "community"],
        "team@ml.org", "machine learning community day", "come along",
    )
    assert hits == 3


def test_an_empty_about_falls_back_to_the_generic_sort():
    for row in INBOX:
        assert bucket_of(*row) in ("promising", "other", "noise")


# ---------------------------------------------------------------------------
# The tool must SAY which question it answered.
# ---------------------------------------------------------------------------
def scan(monkeypatch, about="", rows=INBOX):
    """Run the real scan_inbox against a fake Gmail."""
    class Msg:
        def __init__(self, i, row):
            self.i, self.row = i, row

    def fake_service():
        sender_of = {str(i): r for i, r in enumerate(rows)}

        class Messages:
            def list(self, **kw):
                class R:
                    def execute(_self):
                        return {"messages": [{"id": str(i)} for i in range(len(rows))]}
                return R()

            def get(self, userId, id, **kw):
                sender, subject, snippet = sender_of[id]

                class R:
                    def execute(_self):
                        return {
                            "payload": {"headers": [
                                {"name": "From", "value": sender},
                                {"name": "Subject", "value": subject},
                                {"name": "Date", "value": "Mon, 11 Aug 2026"},
                            ]},
                            "snippet": snippet,
                        }
                return R()

        class Users:
            def messages(self):
                return Messages()

        class Service:
            def users(self):
                return Users()

        return Service()

    from jarvis.tools import gmail

    monkeypatch.setattr(gmail, "_enabled", lambda: True)
    monkeypatch.setattr(gmail, "gmail_service", fake_service)
    return bulkmail.scan_inbox(query="after:2026/08/01", about=about)


def test_a_generic_scan_warns_that_it_is_generic(monkeypatch):
    """
    The heading used to read "WORTH A LOOK", which sounds like an answer to
    whatever was just asked. That is how a generic sort became a specific
    answer.
    """
    out = scan(monkeypatch)
    assert "WARNING" in out
    assert "GENERIC" in out
    assert "do NOT present these buckets as the answer" in out
    assert "WORTH A LOOK:" not in out, "the misleading heading is back"


def test_a_scan_with_about_says_what_it_matched(monkeypatch):
    out = scan(monkeypatch, about="my machine learning community")
    assert "MATCHES WHAT HE ASKED FOR" in out
    assert "machine learning community" in out
    assert "WARNING" not in out


def test_the_right_emails_land_in_the_right_bucket_end_to_end(monkeypatch):
    """
    The whole tool, not just the helper: his community mail above the fold,
    the research outreach below it.
    """
    out = scan(monkeypatch, about="machine learning community")
    head, _, tail = out.partition("EVERYTHING ELSE:")
    assert "ML community meetup" in head
    assert "Speaking slot" in head
    assert "PM2.5 benchmark" in tail, "research outreach is still on top"
    assert "fellowship application" in tail


def test_it_still_tells_him_to_open_anything_close(monkeypatch):
    """
    The sort is keywords on a subject line. Presenting it as a reading of
    the mail is the failure one level up.
    """
    out = scan(monkeypatch, about="machine learning community")
    assert "read_email" in out
    assert "guess" in out.lower()


# ---------------------------------------------------------------------------
# Retrieval still works. The fix must not have broken finding things.
# ---------------------------------------------------------------------------
# The headline tool, not the summary one, for a quick "anything new?" — it
# answers in one spoken sentence. Asserting the summary here was a mistake in
# the test rather than in the router: a count is the right answer to a
# yes/no-shaped question, and the summary is what "summarise my emails" gets.
@pytest.mark.parametrize("phrase, tool", [
    ("what did i miss", "telegram_unread"),
    ("check my email", "unread_email_headline"),
    ("any new emails", "unread_email_headline"),
    ("summarise my emails", "unread_email_summary"),
])
def test_ordinary_email_questions_still_route(phrase, tool):
    from jarvis.brain.router import IntentRouter
    from jarvis.config import CONFIG

    hit = IntentRouter(CONFIG).route(phrase)
    assert hit is not None, f"{phrase!r} stopped routing"
    assert hit.tool == tool


def test_the_brain_is_told_to_pass_about():
    """
    The parameter only helps if the model knows to use it, and the reason
    has to be in the description — a bare "optional" gets skipped.
    """
    from jarvis.brain.tools import TOOL_SPECS

    description, params = TOOL_SPECS["scan_inbox"]
    assert "about" in params
    assert "ALWAYS PASS `about`" in description
    assert "GENERIC" in description
