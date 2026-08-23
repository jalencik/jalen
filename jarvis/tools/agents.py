r"""
Handing work to another AI, properly.

His complaint, twice, and both halves are fair:

  "it still cannot hand this task off to gemini and chatgpt... it should be
   able to chat with chatgpt like a real human, according to the outcome,
   asking my expectations and what I want, and based on the outcome... it
   should compare it against my expectations, and give it another prompt
   that will fix it"

  "it still has major flaws handing tasks off... it should have given a
   master prompt like a prompt engineer with at least 10 years of experience"

TWO SEPARATE FAILURES, AND THE SECOND IS THE INTERESTING ONE
------------------------------------------------------------
The first is plumbing: there was no route to Gemini or ChatGPT at all. That
is just missing code.

The second is quality, and quality cannot be fixed by hoping. Telling a model
"write a good brief" produces a good brief about a third of the time; the
rest of the time it produces two sentences and a cheerful tone. So the
standard is written down (`master_prompt_guide`), and — this is the part that
actually works — `delegate_task` REFUSES a brief that does not meet it, and
says which part is missing. A gate beats an instruction.

THE LOOP HE ASKED FOR
---------------------
    delegate_task     send a proper brief, record what he actually wanted
    read_delegation   what came back
    review_delegation gather the answer, his expectations, and the standard,
                      for the brain to judge - it does NOT judge by itself
    follow_up_task    send the corrections back to the SAME conversation

`review_delegation` deliberately does not score anything. A percentage
computed by counting keywords would be this project's signature bug, and a
confident "90% complete" is exactly the kind of number people stop checking.
The evidence is mechanical; the judgement is the model's, with the original
request in front of it.

WHICH AGENTS, AND WHAT EACH ACTUALLY DOES
-----------------------------------------
    gemini    real API call. Key already in .env, library already installed.
    chatgpt   real API call IF OPENAI_API_KEY is set. Without it, says so
              and offers the clipboard route rather than pretending.
    claude    the local Claude Code CLI, headless, via devwork.
    cowork    the Claude desktop app, via the clipboard (it has no API).

Conversations are kept, so a follow-up is a REPLY rather than a fresh start
that has lost all the context.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
CHATS_PATH = ROOT / "data" / "agent_chats.json"

# What a brief has to contain before it is worth another model's time.
# Enforced, not suggested — see _brief_problems().
MIN_BRIEF_WORDS = 40

# An ALIAS, not a pinned version, and that is deliberate. The first real call
# failed with: "models/gemini-2.5-flash is no longer available to new users,
# please update your code" — a pinned name is a time bomb that goes off
# whenever Google retires a model, and it goes off in HIS hands rather than
# in a test. The alias tracks whatever is current.
GEMINI_MODEL = "gemini-flash-latest"

# Same reasoning. OpenAI retires model names on the same schedule.
OPENAI_MODEL = "gpt-4o-mini"


# ---------------------------------------------------------------------------
# The standard
# ---------------------------------------------------------------------------
MASTER_PROMPT_STANDARD = """\
HOW TO BRIEF ANOTHER AI (the standard this project holds you to)

A brief is not a request. A request says what you want; a brief says what
you want, what "done" means, what must not happen, and what to do when the
instructions run out. The difference is the difference between one round and
five.

Every brief you send MUST have these, each under its own heading:

1. CONTEXT
   Who this is for and what already exists. The other model has never seen
   this machine, this project, or this conversation. One paragraph.

2. OBJECTIVE
   One sentence. What must be true when this is finished that is not true
   now. Not "help with X" - a state of the world.

3. WHAT SUCCESS LOOKS LIKE
   Three to six concrete, checkable items. "The function returns a dict with
   keys a, b, c" is checkable. "Good code" is not. These are what you will
   compare the answer against later, so write them as if someone else will
   do the comparing.

4. CONSTRAINTS
   What it must not do, what it must not assume, what it may not change.
   Include the boring ones: which language, which library, which file, how
   long. Most bad output is a model filling a gap you left.

5. WHAT TO DO IF SOMETHING IS UNCLEAR
   Say explicitly: ask rather than guess, and name the assumption if it has
   to guess anyway. Without this line, models guess silently and confidently.

6. OUTPUT FORMAT
   Exactly what shape the answer should take. If you want code and nothing
   else, say that. If you want a plan first, say that.

RULES THAT MATTER MORE THAN THE STRUCTURE

* Write the CRITERIA before you write the request. If you cannot say how
  you would check it, the brief is not ready and neither are you.
* Give real examples over adjectives. One worked example beats a paragraph
  of "professional, high quality, well-structured".
* Name the failure you are worried about. "Do not invent API endpoints that
  do not exist" prevents a specific, common, expensive mistake.
* State what you already tried and why it did not work, if anything.
* Never say "be thorough" or "think carefully". It changes nothing and
  costs tokens.
"""


def master_prompt_guide() -> str:
    """The house standard for briefing another model — GREEN."""
    return MASTER_PROMPT_STANDARD


def _brief_problems(brief: str) -> list[str]:
    """
    What this brief is missing. Empty list means it is good enough to send.

    A GATE, not a suggestion. The standard above is a paragraph the model can
    read and then ignore; this is a refusal it cannot. He asked for
    prompt-engineer quality, and the only mechanism that reliably produces it
    is not accepting less.
    """
    text = (brief or "").strip()
    problems = []
    if len(text.split()) < MIN_BRIEF_WORDS:
        problems.append(
            f"it is {len(text.split())} words. A brief another model can act "
            f"on without guessing is at least {MIN_BRIEF_WORDS}"
        )
    low = text.lower()
    if not any(k in low for k in ("success", "done when", "criteria",
                                 "should look like", "acceptance")):
        problems.append(
            "there is nothing saying what SUCCESS looks like - without it "
            "neither the other model nor I can tell whether it worked"
        )
    if not any(k in low for k in ("context", "background", "currently",
                                 "existing", "right now", "at the moment")):
        problems.append(
            "there is no CONTEXT - the other model has never seen this "
            "machine or this conversation"
        )
    if not any(k in low for k in ("do not", "don't", "must not", "avoid",
                                 "without", "never", "constraint")):
        problems.append(
            "there are no CONSTRAINTS - most bad output is a model filling a "
            "gap you left"
        )
    return problems


# ---------------------------------------------------------------------------
# Conversation store
# ---------------------------------------------------------------------------
def _load() -> dict[str, dict]:
    try:
        blob = json.loads(CHATS_PATH.read_text(encoding="utf-8"))
        return blob if isinstance(blob, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(chats: dict[str, dict]) -> None:
    try:
        CHATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CHATS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(chats, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, CHATS_PATH)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# The agents themselves
# ---------------------------------------------------------------------------
def _ask_gemini(messages: list[dict]) -> tuple[str, str | None]:
    """(answer, error). Real API call."""
    from ..config import SECRETS

    key = SECRETS.gemini_api_key
    if not key:
        return "", ("GEMINI_API_KEY isn't in your .env. Get one free at "
                    "aistudio.google.com/apikey and add it.")
    try:
        from google import genai

        client = genai.Client(api_key=key)
        prompt = "\n\n".join(
            f"{'YOU SAID' if m['role'] == 'assistant' else 'REQUEST'}:\n{m['content']}"
            for m in messages
        )
        result = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return (result.text or "").strip(), None
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if "PERMISSION_DENIED" in message or "denied access" in message:
            # Seen live on his key. Nothing in this code can fix it, so it
            # says where to go rather than looking like a bug in the handoff.
            return "", (
                "Google is refusing the key in your .env — the project has "
                "been denied access. That is an account problem, not a "
                "problem with the handoff: check aistudio.google.com/apikey, "
                "make a fresh key, and put it in GEMINI_API_KEY. Meanwhile I "
                "can hand this to Claude Code instead."
            )
        if "NOT_FOUND" in message or "not available" in message:
            # The failure this alias exists to prevent, in case it happens
            # anyway. Names the fix rather than just the error.
            return "", (
                f"Google has retired the model I asked for ({GEMINI_MODEL}). "
                "Change GEMINI_MODEL in jarvis/tools/agents.py — run "
                "`client.models.list()` to see what is available."
            )
        return "", f"{type(exc).__name__}: {exc}"


def _ask_chatgpt(messages: list[dict]) -> tuple[str, str | None]:
    """(answer, error). Real API call when a key exists."""
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        return "", (
            "OPENAI_API_KEY isn't in your .env, so I can't talk to ChatGPT "
            "directly. Add one from platform.openai.com/api-keys, or say "
            "'hand this to cowork' and I'll put the brief on your clipboard "
            "and open the app instead."
        )
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        result = client.chat.completions.create(
            model=OPENAI_MODEL, messages=messages,
        )
        return (result.choices[0].message.content or "").strip(), None
    except ImportError:
        return "", ("The openai package isn't installed. Run: "
                    ".venv\\Scripts\\python.exe -m pip install openai")
    except Exception as exc:  # noqa: BLE001
        return "", f"{type(exc).__name__}: {exc}"


AGENTS = {
    "gemini": _ask_gemini,
    "chatgpt": _ask_chatgpt,
    "gpt": _ask_chatgpt,
    "openai": _ask_chatgpt,
}


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------
def delegate_task(agent: str, brief: str, expectation: str = "") -> str:
    """
    Send a properly engineered brief to another AI — AMBER.

    REFUSES a thin brief and says which part is missing. That refusal is the
    feature: he asked for prompt-engineer quality, and the only thing that
    reliably produces it is not accepting less.
    """
    name = (agent or "").strip().lower()
    ask = AGENTS.get(name)
    if ask is None:
        return (
            f"I don't have a route to '{agent}'. I can reach: gemini, "
            "chatgpt (needs an API key), claude code, and the Claude desktop "
            "app via cowork."
        )

    problems = _brief_problems(brief)
    if problems:
        return (
            "That brief isn't ready to send — "
            + "; ".join(problems)
            + ". Call master_prompt_guide and write it properly: context, "
            "objective, what success looks like, constraints, what to do if "
            "something is unclear, and the output format."
        )

    answer, error = ask([{"role": "user", "content": brief}])
    if error:
        return f"Couldn't reach {name}: {error}"
    if not answer:
        return f"{name} returned nothing at all."

    chat_id = uuid.uuid4().hex[:8]
    chats = _load()
    chats[chat_id] = {
        "id": chat_id,
        "agent": name,
        # What HE wanted, in his words, recorded now. Judging afterwards from
        # the agent's own summary is marking its homework against its own
        # answer sheet.
        "expectation": (expectation or brief).strip(),
        "messages": [
            {"role": "user", "content": brief},
            {"role": "assistant", "content": answer},
        ],
        "rounds": 1,
        "started_at": time.time(),
        "started_iso": time.strftime("%Y-%m-%d %H:%M"),
    }
    _save(chats)

    words = len(answer.split())
    return (
        f"{name} answered — {words} words, conversation {chat_id}. "
        f"Say 'review the delegation' and I'll check it against what you "
        f"actually asked for.\n\n{answer}"
    )


def follow_up_task(chat_id: str, message: str) -> str:
    """
    Reply into an existing conversation — AMBER.

    A REPLY, not a fresh start: the whole history goes back with it, so the
    other model still knows what it wrote and why. Starting over is how a
    "fix this one thing" turns into a different answer with new problems.
    """
    chats = _load()
    chat = chats.get((chat_id or "").strip()) or _latest(chats)
    if chat is None:
        return "There's no delegated conversation to follow up on."
    if not (message or "").strip():
        return "Tell me what to send back and I'll continue that conversation."

    ask = AGENTS.get(chat["agent"])
    if ask is None:
        return f"I've lost the route to {chat['agent']}."

    chat["messages"].append({"role": "user", "content": message})
    answer, error = ask(chat["messages"])
    if error:
        chat["messages"].pop()
        _save(chats)
        return f"Couldn't reach {chat['agent']}: {error}"

    chat["messages"].append({"role": "assistant", "content": answer})
    chat["rounds"] = chat.get("rounds", 1) + 1
    _save(chats)
    return (
        f"Round {chat['rounds']} with {chat['agent']} "
        f"(conversation {chat['id']}):\n\n{answer}"
    )


def _latest(chats: dict[str, dict]) -> dict | None:
    return max(chats.values(), key=lambda c: c.get("started_at", 0)) if chats else None


def list_delegations(limit: int = 10) -> str:
    """Conversations with other AIs — GREEN."""
    chats = sorted(_load().values(), key=lambda c: c.get("started_at", 0),
                   reverse=True)[:limit]
    if not chats:
        return "I haven't handed anything to another AI yet."
    return "\n".join(
        f"  {c['id']}  {c['agent']}  {c.get('rounds', 1)} round(s)  "
        f"- {c.get('expectation', '')[:60]}"
        for c in chats
    )


def review_delegation(chat_id: str = "") -> str:
    """
    Everything needed to judge whether the other AI did the job — GREEN.

    Deliberately does NOT conclude. He asked for "how many percent of my
    expectations has been met", and a number produced by counting keywords
    would be exactly this project's recurring bug. So this lays out what he
    wanted, what was asked, and what came back, and tells the brain to judge
    — with the original request in front of it.
    """
    chats = _load()
    chat = chats.get((chat_id or "").strip()) or _latest(chats)
    if chat is None:
        return "There's nothing delegated to review."

    last_answer = ""
    for message in reversed(chat["messages"]):
        if message["role"] == "assistant":
            last_answer = message["content"]
            break

    return "\n".join([
        f"DELEGATION {chat['id']} to {chat['agent']} - {chat.get('rounds', 1)} round(s)",
        "",
        "WHAT HE ACTUALLY WANTED (recorded before it was sent):",
        f"  {chat.get('expectation', '')}",
        "",
        "WHAT WAS ASKED:",
        f"  {chat['messages'][0]['content'][:2000]}",
        "",
        "WHAT CAME BACK (the most recent answer):",
        last_answer[:8000] or "  (nothing)",
        "",
        "NOW JUDGE IT, and be specific:",
        "  1. Go through what he wanted, point by point, and say for each "
        "whether it is DONE, PARTLY done, or MISSING.",
        "  2. Give a rough proportion, and say what it is based on.",
        "  3. Name anything the answer CLAIMS that it has not actually shown.",
        "  4. If anything is missing or wrong, WRITE THE FOLLOW-UP PROMPT: "
        "name the specific gaps, do not re-state the whole task, and send it "
        "with follow_up_task so the other model still has its own context.",
        "  5. Say the short version out loud; the detail goes on screen.",
    ])


REGISTRY: dict[str, Any] = {
    "master_prompt_guide": master_prompt_guide,
    "delegate_task": delegate_task,
    "follow_up_task": follow_up_task,
    "list_delegations": list_delegations,
    "review_delegation": review_delegation,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
