"""
Reading a hundred emails in one call.

He hit this wall in a real session: "could you please continue reading all
of my emails? I mean, you gotta read 100 emails at least." search_email caps
at 25, so Jalen ground through five separate searches with the turn limit
closing in, and then had to describe twenty-three polite declines out loud.
"""
from __future__ import annotations

import pytest

from jarvis.tools.bulkmail import HARD_CAP, PAGE_SIZE, _classify


@pytest.mark.parametrize(
    "sender, subject, snippet",
    [
        ("Tan Yan Shuo <yanshuo@nus.edu.sg>",
         "Re: interpretable tree-based machine learning", "Thanks for reaching out"),
        ("Kevin Zhu <kevin@algoverseairesearch.org>",
         "sit in on our first lecture", "apply for the August cohort"),
        ("Prof Lee <lee@ontariotechu.ca>",
         "Lab opportunity for NLP students", "we have a research position"),
        ("grants@nsf.gov", "Your fellowship application", "shortlisted for interview"),
    ],
)
def test_real_opportunities_are_surfaced(sender, subject, snippet):
    assert _classify(sender, subject, snippet) == "promising"


@pytest.mark.parametrize(
    "sender, subject, snippet",
    [
        ("LinkedIn <notifications-noreply@linkedin.com>",
         "You have 3 new job opportunities", "See who viewed your profile"),
        ("Impactpool <service@email.impactpool.org>",
         "Job Opportunities", "Here are your latest recommended opportunities"),
        ("UMN Admissions <admissions@apply.umn.edu>",
         "Jaloliddin, have you met Goldy?", "A friendly Gopher hello"),
        ("Discord <noreply@discord.com>", "New message", "You have unread messages"),
    ],
)
def test_bulk_mail_is_not_surfaced_as_an_opportunity(sender, subject, snippet):
    """
    Every one of these contains the right WORDS. Two were live false
    positives on his real inbox — a university admissions blast and a jobs
    board — which is why the sender address is checked before the words.
    """
    assert _classify(sender, subject, snippet) == "noise"


def test_the_sender_is_judged_before_the_words():
    """
    "Opportunity" appears in most marketing subject lines ever written. If
    the words were trusted first, a dedicated mail subdomain would still
    land in the pile he reads.
    """
    assert _classify("x@email.university.edu", "Research opportunity", "apply now") == "noise"
    assert _classify("prof@university.edu", "Research opportunity", "apply now") == "promising"


def test_ordinary_mail_is_neither():
    assert _classify("friend@gmail.com", "lunch tomorrow?", "are you free") == "other"


def test_the_page_size_beats_the_cap_it_exists_to_replace():
    """
    The whole reason this tool exists: search_email clamps to 25, and "read
    100 emails" through that costs five tool calls out of a budget of thirty.
    """
    assert PAGE_SIZE >= 100
    assert HARD_CAP >= 300


def test_it_is_reachable_and_read_only():
    from jarvis import tools
    from jarvis.brain.tools import TOOL_SPECS
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine, Tier

    assert "scan_inbox" in tools.REGISTRY
    assert "scan_inbox" in TOOL_SPECS
    assert SafetyEngine(CONFIG).classify("scan_inbox", {}).tier is Tier.GREEN


def test_the_turn_budget_fits_a_real_research_task():
    """
    Twelve turns was chosen when a turn meant "open Chrome". Reading a
    hundred emails, researching a lab, writing a post and saving it is a
    dozen tool calls before it has done anything wrong — and he watched it
    run out mid-task.
    """
    from jarvis.config import CONFIG

    assert CONFIG.get_path("brain.max_turns_per_request", 12) >= 25


def test_the_sorting_admits_it_is_a_guess():
    """
    A confident wrong classification sends him to read a marketing email as
    an opportunity, or worse, buries a real one. The tool says which it is.
    """
    import inspect

    from jarvis.tools.bulkmail import scan_inbox

    source = inspect.getsource(scan_inbox)
    assert "guess" in source.lower()
    assert "read_email" in source, "it doesn't tell the brain how to check"
