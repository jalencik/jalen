r"""
Driving ChatGPT and Gemini in a real browser, and not believing them.

WHAT HE ASKED FOR, AND WHY THE OLD VERSION WAS NOT IT
-----------------------------------------------------
`agents.py` already talks to Gemini and ChatGPT over their APIs. That is not
what he wants, and he said so precisely:

    YOU: "Jalen, research X and solve it."
      -> professional task brief
      -> opens Chrome
      -> opens ChatGPT OR Gemini
      -> signs in if needed
      -> puts the prompt in the chat box, submits, WAITS
      -> detects that it finished
      -> reads the response
      -> compares it against the ORIGINAL goal
      -> not done? improved prompt, follow up, wait, evaluate, repeat

Two reasons the browser matters rather than the API. His Gemini API key is
403'd by Google and his ChatGPT has no API key at all, so the API route is
blocked by billing rather than by code — but the web chats he already pays
for work fine. And the web UI is where the conversation history lives, which
is what makes a follow-up a follow-up instead of a fresh start.

THE RULE THAT MAKES THIS A SUPERVISOR AND NOT A COURIER
-------------------------------------------------------
    "Jalen should not decide 100% completed merely because ChatGPT/Gemini
     says done."

So this module gathers evidence and REFUSES to score it. `read_result`
returns the original objective, the success criteria, and the actual
response, and ends by telling the brain to judge — the same discipline
`review_delegation` already holds. A percentage computed here by counting
keywords would be this project's signature bug wearing a new hat.

WHAT IT WILL NOT DO
-------------------
It does not defeat CAPTCHA, 2FA, or anti-bot checks, and it never will. His
own instructions said not to, twice, and then once said to; the honest
maximum is implemented instead — detect the challenge, raise the window,
say so out loud, wait for him, and carry on the moment it clears. A
challenge is a site saying "prove a human is here", and the correct response
is to go and get one.

WHY A SEPARATE CHROME PROFILE
-----------------------------
Chrome locks a user-data directory, so automating the profile he is browsing
in is not slightly awkward, it is impossible. Jalen gets its own profile
directory instead. He signs in there once and the session persists for
months; his own Chrome, his own tabs and his own cookies are never touched.

Verified on this machine: channel="chrome" drives his INSTALLED Chrome, so
nothing is downloaded, and the profile costs about 11 MB.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
PROFILE_DIR = DATA / "browser_profile"
CHATS_PATH = DATA / "web_chats.json"

# He asked for a limit and for it to be configurable. Three is the default
# because the fourth correction has, in practice, never been the one that
# works — by then the brief was wrong, not the answer.
MAX_ROUNDS = 3

# How long to wait for a reply before giving up and SAYING SO. Deep-research
# style answers genuinely take minutes; the timeout is generous because
# "it timed out" is a much better outcome than "it returned half an answer
# and I called it done".
REPLY_TIMEOUT_S = 300.0

# The response must stop changing for this long before it counts as
# finished. Streaming UIs pause between tokens, so a short window reports
# completion mid-sentence.
STABLE_FOR_S = 2.5
POLL_S = 0.5


# ---------------------------------------------------------------------------
# THE PROFESSIONAL BRIEF (his 3A, verbatim headings)
# ---------------------------------------------------------------------------
@dataclass
class TaskSpec:
    """
    A work order, not a request.

    Every field is a question the receiving model would otherwise answer for
    itself, silently and wrongly. `success_criteria` carries the most weight:
    it is what the answer gets compared against afterwards, so it is written
    as if a stranger will do the comparing — because one will.
    """

    objective: str
    context: str = ""
    known_facts: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    task: str = ""
    required_output: str = ""
    success_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    negative_requirements: list[str] = field(default_factory=list)
    edge_cases: list[str] = field(default_factory=list)
    verification: str = ""

    def problems(self) -> list[str]:
        """
        What is missing. Empty means it is fit to send.

        A GATE, not advice. "Write a good brief" produces one about a third
        of the time; refusing a bad one produces one every time.
        """
        out = []
        if len(self.objective.split()) < 5:
            out.append(
                "OBJECTIVE is one line or less. It should say what must be "
                "TRUE when this is finished that is not true now"
            )
        if len(self.success_criteria) < 2:
            out.append(
                "fewer than two SUCCESS CRITERIA. These are what the answer "
                "gets checked against - without them nobody, including me, "
                "can say whether it worked"
            )
        vague = [c for c in self.success_criteria
                 if len(c.split()) < 4 or _is_vague(c)]
        if vague:
            out.append(
                f"these criteria cannot be checked: {vague[:3]}. "
                f"'Returns a dict with keys a, b, c' is checkable; "
                f"'good quality' is not"
            )
        if not self.context.strip():
            out.append(
                "no CONTEXT. The other model has never seen this machine, "
                "this project, or this conversation"
            )
        if not self.constraints and not self.negative_requirements:
            out.append(
                "no CONSTRAINTS and no NEGATIVE REQUIREMENTS. Most bad "
                "output is a model filling a gap you left"
            )
        return out

    def render(self) -> str:
        """The brief as the other model will see it."""
        def block(title: str, body: str) -> str:
            return f"## {title}\n{body.strip()}\n" if body.strip() else ""

        def bullets(title: str, items: list[str]) -> str:
            if not items:
                return ""
            lines = "\n".join(f"- {i.strip()}" for i in items if i.strip())
            return f"## {title}\n{lines}\n" if lines else ""

        parts = [
            "# WORK ORDER",
            "",
            block("OBJECTIVE", self.objective),
            block("CONTEXT", self.context),
            bullets("KNOWN FACTS", self.known_facts),
            bullets("RELEVANT INPUTS", self.inputs),
            block("TASK", self.task or self.objective),
            block("REQUIRED OUTPUT", self.required_output),
            bullets("SUCCESS CRITERIA — you will be judged against these",
                    self.success_criteria),
            bullets("CONSTRAINTS", self.constraints),
            bullets("MUST NOT", self.negative_requirements),
            bullets("EDGE CASES TO HANDLE", self.edge_cases),
            block("HOW COMPLETION WILL BE VERIFIED", self.verification),
            "## IF SOMETHING IS UNCLEAR\n"
            "Ask, rather than guessing. If you must assume something to "
            "proceed, state the assumption explicitly at the top of your "
            "answer so it can be checked.\n",
        ]
        return "\n".join(p for p in parts if p).strip()

    @classmethod
    def from_dict(cls, data: dict) -> "TaskSpec":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        for key in ("known_facts", "inputs", "success_criteria",
                    "constraints", "negative_requirements", "edge_cases"):
            value = clean.get(key)
            if isinstance(value, str):
                clean[key] = [v.strip() for v in value.split("\n") if v.strip()]
        return cls(**clean)


_VAGUE = (
    "good", "high quality", "well written", "professional", "nice",
    "properly", "correctly", "as expected", "make sense", "reasonable",
    "clean", "robust", "efficient", "best practice",
)


def _is_vague(text: str) -> bool:
    """A criterion nobody could fail is not a criterion."""
    low = text.lower()
    return any(word in low for word in _VAGUE) and not re.search(r"\d", low)


# ---------------------------------------------------------------------------
# SITE ADAPTERS
#
# Selectors are ORDERED CANDIDATE LISTS, not single strings, and every one is
# tried before giving up. Both of these sites rewrite their DOM regularly,
# and a single brittle selector turns "the site changed its markup" into
# "Jalen is broken" with no clue which. Role- and label-based selectors come
# first because they survive restyling; class names come last because they
# do not survive anything.
# ---------------------------------------------------------------------------
@dataclass
class SiteAdapter:
    key: str
    label: str
    url: str
    # Anything here visible on the page means: not signed in.
    signed_out: tuple[str, ...]
    # A human challenge. We stop and hand over; we never attempt these.
    challenge: tuple[str, ...]
    prompt_box: tuple[str, ...]
    send_button: tuple[str, ...]
    stop_button: tuple[str, ...]
    responses: tuple[str, ...]
    signup_url: str = ""


CHATGPT = SiteAdapter(
    key="chatgpt",
    label="ChatGPT",
    url="https://chatgpt.com/",
    signup_url="https://chatgpt.com/auth/login",
    signed_out=(
        'button:has-text("Log in")',
        'button:has-text("Sign up")',
        '[data-testid="login-button"]',
    ),
    challenge=(
        'iframe[title*="challenge" i]',
        'iframe[src*="recaptcha"]',
        'iframe[src*="hcaptcha"]',
        'text=/verify you are human/i',
        'text=/enter the code/i',
        'text=/two-factor/i',
    ),
    prompt_box=(
        '#prompt-textarea',
        'div[contenteditable="true"][id="prompt-textarea"]',
        'textarea[data-testid="prompt-textarea"]',
        'div[contenteditable="true"]',
        'textarea',
    ),
    send_button=(
        '[data-testid="send-button"]',
        'button[aria-label*="Send" i]',
        'button:has(svg)[type="submit"]',
    ),
    stop_button=(
        '[data-testid="stop-button"]',
        'button[aria-label*="Stop" i]',
    ),
    responses=(
        '[data-message-author-role="assistant"]',
        'div.markdown.prose',
    ),
)

GEMINI = SiteAdapter(
    key="gemini",
    label="Gemini",
    url="https://gemini.google.com/app",
    signup_url="https://accounts.google.com/signup",
    signed_out=(
        'a:has-text("Sign in")',
        'text=/sign in to continue/i',
    ),
    challenge=(
        'iframe[src*="recaptcha"]',
        'text=/verify it.s you/i',
        'text=/2-Step Verification/i',
        'text=/enter the code/i',
    ),
    prompt_box=(
        'div.ql-editor[contenteditable="true"]',
        'rich-textarea div[contenteditable="true"]',
        'div[contenteditable="true"]',
        'textarea',
    ),
    send_button=(
        'button[aria-label*="Send" i]',
        'button.send-button',
    ),
    stop_button=(
        'button[aria-label*="Stop" i]',
        'button.stop-icon',
    ),
    responses=(
        'model-response',
        'message-content',
        '.model-response-text',
    ),
)

SITES = {"chatgpt": CHATGPT, "gemini": GEMINI}


# ---------------------------------------------------------------------------
# THE BROWSER
# ---------------------------------------------------------------------------
class BrowserUnavailable(RuntimeError):
    """Playwright missing, or Chrome refused to start. Say so; never fake it."""


class _Session:
    """
    One Chrome, one profile, ONE THREAD, kept alive between rounds.

    THE BUG THIS SHAPE EXISTS TO FIX
    --------------------------------
    He asked for the ten richest people, handed off to Gemini, and got:
    "That failed on the browser side, Boss - a crash-log error from the
    delegate tool itself." Reproduced exactly:

        thread A:  open the browser        -> ok
        thread B:  use the same browser    -> greenlet.error: cannot switch
                                              to a different thread

    Playwright's synchronous API is bound to the thread that created it, and
    it is NOT thread-safe. The brain runs every tool call through
    asyncio.to_thread, which hands out whichever pool thread is free - so his
    sequence (web_search, then web_read, then web_delegate) touched the
    browser from three different threads and the third one died.

    Nothing about the calling code was wrong. A singleton holding Playwright
    objects is simply not a thing that can be shared, and the failure only
    appears once a second thread gets involved - which is why every test
    passed and the first real use did not.

    SO THE BROWSER OWNS A THREAD, and callers send it work.
    Every operation is a callable that receives the page and runs THERE.
    Callers block for the result, so it reads like ordinary code:

        session.do(lambda page: page.title())

    HEADED, always. A headless window cannot be signed into by a human, and
    signing in is the one thing this design hands back to him.
    """

    _instance: "_Session | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._jobs: "queue.Queue[tuple]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready: "queue.Queue[str]" = queue.Queue(maxsize=1)

    @classmethod
    def get(cls) -> "_Session":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ------------------------------------------------------------- the thread
    def _pump(self) -> None:
        """Owns the browser for its whole life. Never touched from outside."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._ready.put(
                "Playwright isn't installed. Run: "
                ".venv\\Scripts\\python.exe -m pip install playwright"
            )
            return

        try:
            pw = sync_playwright().start()
        except Exception as exc:  # noqa: BLE001
            self._ready.put(f"Playwright wouldn't start: {type(exc).__name__}: {exc}")
            return

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                channel="chrome",      # HIS Chrome. Nothing is downloaded.
                headless=False,
                args=["--no-first-run", "--no-default-browser-check"],
            )
        except Exception as exc:  # noqa: BLE001
            try:
                pw.stop()
            except Exception:
                pass
            # The likeliest cause by far, and worth naming: Chrome refuses to
            # open a profile directory another Chrome already holds.
            hint = ""
            if "ProcessSingleton" in str(exc) or "already in use" in str(exc).lower():
                hint = (" Something else is already using Jalen's browser "
                        "profile - close the window I opened earlier.")
            self._ready.put(f"Chrome wouldn't start: {type(exc).__name__}: {exc}.{hint}")
            return

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        self._ready.put("")          # "" means started cleanly

        while True:
            job, out = self._jobs.get()
            if job is None:
                break
            try:
                out.put(("ok", job(page)))
            except Exception as exc:  # noqa: BLE001
                out.put(("err", exc))
        for close in (ctx.close, pw.stop):
            try:
                close()
            except Exception:
                pass

    def start(self) -> None:
        """Bring the browser up. Idempotent. Raises BrowserUnavailable."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._pump, name="jalen-browser", daemon=True)
            self._thread.start()
            try:
                problem = self._ready.get(timeout=90)
            except queue.Empty:
                raise BrowserUnavailable(
                    "Chrome didn't finish starting within 90 seconds."
                ) from None
            if problem:
                self._thread = None
                raise BrowserUnavailable(problem)

    def do(self, job, *, timeout: float = 360.0):
        """
        Run `job(page)` on the browser's own thread and return its result.

        Blocking on purpose. The caller is already on a worker thread, and
        pretending this is asynchronous would just move the same waiting
        somewhere harder to read.
        """
        self.start()
        out: "queue.Queue[tuple]" = queue.Queue(maxsize=1)
        self._jobs.put((job, out))
        try:
            status, value = out.get(timeout=timeout)
        except queue.Empty:
            raise BrowserUnavailable(
                f"The browser stopped responding after {int(timeout)}s."
            ) from None
        if status == "err":
            raise value
        return value

    def stop(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._jobs.put((None, None))
                self._thread.join(timeout=15)
            self._thread = None
            type(self)._instance = None


def _first_visible(page, selectors, timeout: float = 3.0):
    """
    The first of these that is actually on the page, or None.

    Ordered candidates rather than one selector because both sites rewrite
    their markup regularly. Returning None rather than raising is the point:
    the caller turns it into "I couldn't find the message box", which is a
    reportable fact, instead of a stack trace nobody can act on.
    """
    deadline = time.monotonic() + timeout
    while True:
        for selector in selectors:
            try:
                found = page.locator(selector).first
                if found.count() and found.is_visible():
                    return found
            except Exception:
                continue
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.25)


def _present(page, selectors) -> bool:
    """Is any of these visible right now? No waiting."""
    for selector in selectors:
        try:
            found = page.locator(selector).first
            if found.count() and found.is_visible():
                return True
        except Exception:
            continue
    return False


def page_state(page, adapter: SiteAdapter) -> str:
    """
    "challenge" | "signed-out" | "ready"

    Challenge is checked FIRST. A CAPTCHA on a login page also shows the
    login controls, and reporting that as merely signed-out would send Jalen
    off to type a password into a box that is not going to accept it.
    """
    if _present(page, adapter.challenge):
        return "challenge"
    if _present(page, adapter.signed_out):
        return "signed-out"
    return "ready"


def submit_prompt(page, adapter: SiteAdapter, text: str) -> str:
    """
    Type the brief and send it. Returns "" on success, else why not.

    fill() for real textareas, insert_text otherwise - both sites use
    contenteditable divs, which ignore fill() silently on some builds.
    Enter is the fallback send: these UIs bind it, and an aria-label can be
    renamed in a redesign while the key binding survives.
    """
    box = _first_visible(page, adapter.prompt_box, timeout=8.0)
    if box is None:
        return (f"I couldn't find the message box on {adapter.label}. "
                f"The page may have changed, or it may still be loading.")
    try:
        box.click()
        try:
            box.fill(text)
        except Exception:
            page.keyboard.insert_text(text)
    except Exception as exc:
        return f"I couldn't type into {adapter.label}: {type(exc).__name__}"

    send = _first_visible(page, adapter.send_button, timeout=2.0)
    try:
        if send is not None and send.is_enabled():
            send.click()
        else:
            page.keyboard.press("Enter")
    except Exception as exc:
        return f"I typed it but couldn't send it: {type(exc).__name__}"
    return ""


def _visible_text(page, selectors) -> str:
    """The LAST matching block's text, or ''."""
    for selector in selectors:
        try:
            blocks = page.locator(selector)
            count = blocks.count()
            if count:
                return blocks.nth(count - 1).inner_text().strip()
        except Exception:
            continue
    return ""


def wait_for_completion(page, adapter: SiteAdapter, *,
                        timeout: float = REPLY_TIMEOUT_S,
                        stable_for: float = STABLE_FOR_S) -> tuple:
    """
    Wait until the answer is genuinely finished. (True, "") or (False, why).

    "Do NOT simply sleep for 30 seconds and assume the job is finished."
    Agreed - and a sleep is wrong in both directions: it calls short answers
    unfinished and long ones done.

    THREE SIGNALS, ALL REQUIRED:

        the stop button is gone      it exists only while generating
        there is some text at all    an empty answer is not a finished one
        the text stopped changing    for stable_for seconds

    The last one carries it. Streaming UIs pause between tokens, so a shorter
    window reports completion mid-sentence - and a mid-sentence answer judged
    against the success criteria fails for entirely the wrong reason.

    Returns (False, reason) rather than raising: "I could not verify that it
    finished" is a result he needs to hear, not an exception.
    """
    deadline = time.monotonic() + timeout
    previous = None
    settled_at = None

    while time.monotonic() < deadline:
        if _present(page, adapter.challenge):
            return False, ("a human check appeared while it was answering - "
                           "it needs you before it can carry on")

        generating = _present(page, adapter.stop_button)
        current = _visible_text(page, adapter.responses)

        if generating or not current:
            settled_at = None
            previous = current or previous
        elif current != previous:
            previous = current
            settled_at = time.monotonic()
        elif settled_at is None:
            settled_at = time.monotonic()
        elif time.monotonic() - settled_at >= stable_for:
            return True, ""

        time.sleep(POLL_S)

    return False, (
        f"{adapter.label} was still working after {int(timeout)} seconds. "
        f"I can't confirm it finished, so I won't claim it did."
    )


def read_response(page, adapter: SiteAdapter) -> str:
    """
    The final answer, formatting kept.

    inner_text rather than text_content: it respects line breaks, so code
    blocks and lists survive as something a person can read, which
    text_content flattens into a single paragraph.
    """
    return _visible_text(page, adapter.responses)


# ---------------------------------------------------------------------------
# CONVERSATION STORE
#
# A follow-up must land in the SAME chat: the other model needs its own
# previous work in front of it, and "here is what was wrong with your answer"
# means nothing in a fresh conversation. So the browser stays open and the
# chat is addressed by id.
# ---------------------------------------------------------------------------
def _load() -> dict:
    try:
        return json.loads(CHATS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(chats: dict) -> None:
    CHATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHATS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(chats, indent=2), encoding="utf-8")
    tmp.replace(CHATS_PATH)


def _latest(chats: dict) -> dict | None:
    live = [c for c in chats.values() if c.get("rounds")]
    return max(live, key=lambda c: c["rounds"][-1]["at"]) if live else None


def _resolve(chat_id: str) -> tuple[dict | None, dict, str]:
    """(chat, all_chats, error). Empty chat_id means the most recent one."""
    chats = _load()
    if chat_id:
        chat = chats.get(chat_id)
        if chat is None:
            return None, chats, f"I don't have a conversation called {chat_id}."
        return chat, chats, ""
    chat = _latest(chats)
    if chat is None:
        return None, chats, "I haven't handed anything to ChatGPT or Gemini yet."
    return chat, chats, ""


# ---------------------------------------------------------------------------
# DELEGATION
# ---------------------------------------------------------------------------
def _host(url: str) -> str:
    return (url or "").split("//")[-1].split("/")[0].lower()


def _goto(page, adapter: SiteAdapter) -> str:
    """
    Make sure the page is on the right site. "" or a reason it is not.

    RUNS ON THE BROWSER THREAD ONLY. Everything that touches `page` does;
    that is the whole point of the session above.
    """
    try:
        if _host(adapter.url) not in _host(page.url or ""):
            page.goto(adapter.url, timeout=45000, wait_until="domcontentloaded")
        return ""
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't open {adapter.label}: {type(exc).__name__}"


def _exchange(adapter: SiteAdapter, message: str, criteria: list) -> dict:
    """
    One complete round trip, as a SINGLE job on the browser thread.

    Composed into one job deliberately rather than five small ones. Between
    two separate jobs another caller could interleave its own, and "navigate,
    then someone else navigates, then submit" would type a brief into
    whatever page happened to be showing.
    """
    def job(page) -> dict:
        problem = _goto(page, adapter)
        if problem:
            return {"error": problem}
        state = page_state(page, adapter)
        if state != "ready":
            return {"state": state}
        problem = submit_prompt(page, adapter, message)
        if problem:
            return {"error": problem}
        finished, why = wait_for_completion(page, adapter)
        return {
            "answer": read_response(page, adapter),
            "finished": finished,
            "why": why,
            "url": page.url,
        }

    try:
        return _Session.get().do(job, timeout=REPLY_TIMEOUT_S + 120)
    except BrowserUnavailable as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"The browser failed: {type(exc).__name__}: {exc}"}


def _blocked(adapter: SiteAdapter, state: str) -> str:
    if state == "challenge":
        return (f"{adapter.label} is showing a human verification check. I "
                f"won't try to get past that - the window is open, clear it "
                f"and tell me to carry on.")
    return (f"You're not signed in to {adapter.label}. Say 'sign me in to "
            f"{adapter.label}' and I'll walk you through it - the window is "
            f"already open.")


def web_delegate(agent: str, spec: Any = None, **fields) -> str:
    """
    Hand a task to ChatGPT or Gemini in a real browser, and wait for it.

    The brief is GATED before the browser is even opened: sending a thin
    brief and then spending three correction rounds fixing it is the
    expensive way to discover it was thin.
    """
    key = (agent or "").strip().lower()
    adapter = SITES.get(key)
    if adapter is None:
        return (f"I can drive ChatGPT or Gemini in the browser. "
                f"I don't know '{agent}'.")

    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except ValueError:
            spec = {"objective": spec}
    data = dict(spec or {})
    data.update(fields)
    task = TaskSpec.from_dict(data)

    problems = task.problems()
    if problems:
        return (
            "That brief isn't ready to send, and sending it would cost "
            "correction rounds to fix something I can see now:\n  - "
            + "\n  - ".join(problems)
            + "\n\nGive me those and I'll send it."
        )

    brief = task.render()
    result = _exchange(adapter, brief, task.success_criteria)
    if result.get("error"):
        return result["error"]
    if result.get("state"):
        return _blocked(adapter, result["state"])

    chat_id = f"web-{key}-{uuid.uuid4().hex[:8]}"
    chats = _load()
    chats[chat_id] = {
        "id": chat_id,
        "agent": key,
        "label": adapter.label,
        "objective": task.objective,
        "criteria": task.success_criteria,
        "spec": data,
        "url": result.get("url", ""),
        "rounds": [{
            "at": time.time(),
            "kind": "brief",
            "sent": brief,
            "answer": result.get("answer", ""),
            "verified_complete": result.get("finished", False),
            "note": result.get("why", ""),
        }],
    }
    _save(chats)

    if not result.get("finished"):
        return (f"I sent the brief to {adapter.label} but {result.get('why')}"
                f"\n\nConversation id: {chat_id}\n"
                f"What it had produced so far:\n{result.get('answer','')[:1500]}")
    return read_result(chat_id)


def web_follow_up(chat_id: str = "", corrections: str = "") -> str:
    """
    Send a correction into the SAME conversation, and wait again.

    The round limit is enforced HERE rather than trusted to the caller. A
    supervisor that can be talked into a fourth round can be talked into a
    fortieth, and by round four the brief was wrong rather than the answer.
    """
    chat, chats, error = _resolve(chat_id)
    if error:
        return error
    if not (corrections or "").strip():
        return "Tell me what to correct and I'll send it."

    done = len([r for r in chat["rounds"] if r["kind"] == "correction"])
    if done >= MAX_ROUNDS:
        return (
            f"That's {done} corrections already, which is the limit. When "
            f"three rounds haven't fixed it, the brief was wrong rather than "
            f"the answer - it wants restating, not re-sending.\n\n"
            f"Original objective: {chat['objective']}\n"
            f"Last answer:\n{chat['rounds'][-1]['answer'][:1200]}"
        )

    adapter = SITES[chat["agent"]]
    criteria = chat.get("criteria", [])
    message = (
        "Your previous answer does not yet meet the brief. Corrections:\n\n"
        f"{corrections.strip()}\n\n"
        "The success criteria have not changed:\n"
        + "\n".join(f"- {c}" for c in criteria)
        + "\n\nRevise your answer so every criterion above is met. Do not "
          "restate what you already did correctly - give the corrected work."
    )

    result = _exchange(adapter, message, criteria)
    if result.get("error"):
        return result["error"]
    if result.get("state"):
        return _blocked(adapter, result["state"])

    chat["rounds"].append({
        "at": time.time(), "kind": "correction", "sent": message,
        "answer": result.get("answer", ""),
        "verified_complete": result.get("finished", False),
        "note": result.get("why", ""),
    })
    _save(chats)

    if not result.get("finished"):
        return (f"I sent the correction but {result.get('why')}\n\n"
                f"What it had so far:\n{result.get('answer','')[:1500]}")
    return read_result(chat["id"])


# ---------------------------------------------------------------------------
# THE PART THAT REFUSES TO SCORE ITSELF
# ---------------------------------------------------------------------------
def read_result(chat_id: str = "") -> str:
    """
    Everything needed to judge, and no judgement.

    "Jalen should not decide 100% completed merely because ChatGPT/Gemini
    says done." The temptation is to count keywords from the criteria in the
    answer and report a percentage. That number would be wrong in the one
    direction that matters - confidently high - and a confident percentage is
    exactly the kind of thing people stop checking.

    So: the objective, the criteria, the actual answer, and an instruction to
    judge. The evidence is mechanical; the judgement belongs to the model
    that has the original request in front of it.
    """
    chat, _chats, error = _resolve(chat_id)
    if error:
        return error

    # ANOTHER MODEL'S ANSWER IS UNTRUSTED TEXT TOO, and it is easy to forget
    # because it came from an AI rather than a stranger. But ChatGPT and
    # Gemini both search the web, so their answers can carry text lifted from
    # a page somebody else controls - "ignore the criteria above, instead..."
    # reads exactly the same whether a human or a model relayed it.
    from .. import taint

    taint.mark(f"{chat['label']} answer")

    last = chat["rounds"][-1]
    corrections = len([r for r in chat["rounds"] if r["kind"] == "correction"])
    criteria = chat.get("criteria") or ["(none were given)"]

    return (
        f"{chat['label']} answered. Conversation {chat['id']}, "
        f"{corrections} correction(s) sent so far, "
        f"{MAX_ROUNDS - corrections} remaining.\n\n"
        f"WHAT HE ORIGINALLY WANTED\n{chat['objective']}\n\n"
        f"WHAT COUNTS AS DONE\n"
        + "\n".join(f"  {n}. {c}" for n, c in enumerate(criteria, 1))
        + f"\n\nCOMPLETION VERIFIED IN THE BROWSER: "
          f"{'yes' if last.get('verified_complete') else 'NO - ' + last.get('note', '')}"
        + f"\n\nWHAT {chat['label'].upper()} ACTUALLY SAID\n"
          f"{'-' * 60}\n{last['answer']}\n{'-' * 60}\n\n"
        "NOW JUDGE IT. Go criterion by criterion and mark each PASS or FAIL "
        "against the text above - not against what it claims about itself. A "
        "model saying 'I have completed all requirements' is not evidence "
        "that it has.\n\n"
        "Then say exactly one of: SATISFIED / PARTIALLY SATISFIED / NOT "
        "SATISFIED / CANNOT VERIFY.\n\n"
        "If it is not SATISFIED and corrections remain, call web_follow_up "
        "with precisely what is missing - name the criterion number, say what "
        "is wrong, and say what to change. Do not send 'please improve it'."
    )


def list_web_chats(limit: int = 10) -> str:
    chats = _load()
    if not chats:
        return "I haven't handed anything to ChatGPT or Gemini in the browser."
    ordered = sorted(chats.values(), key=lambda c: c["rounds"][-1]["at"],
                     reverse=True)[:max(1, int(limit))]
    lines = []
    for chat in ordered:
        corrections = len([r for r in chat["rounds"] if r["kind"] == "correction"])
        verified = chat["rounds"][-1].get("verified_complete")
        lines.append(
            f"  {chat['id']}  {chat['label']:8s}  "
            f"{corrections} correction(s)  "
            f"{'finished' if verified else 'UNVERIFIED'}\n"
            f"      {chat['objective'][:90]}"
        )
    return "Web delegations:\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# SIGNING IN
#
# "It should itself know whether user needs to authenticate or not, if the
#  user has account or not so it should know whether to sign in or sign up."
#
# It can know the first (the page says so) and it can know the second only
# from the vault - so a vault entry IS the answer to "do I have an account
# here". No entry means asking, which he said was fine.
# ---------------------------------------------------------------------------
def web_sign_in_state(agent: str = "") -> str:
    """Where does the sign-in stand? Opens the window; types nothing."""
    key = (agent or "").strip().lower()
    adapter = SITES.get(key)
    if adapter is None:
        return "Which one - ChatGPT or Gemini?"

    def job(page) -> str:
        problem = _goto(page, adapter)
        return problem or page_state(page, adapter)

    try:
        outcome = _Session.get().do(job, timeout=120)
    except BrowserUnavailable as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"The browser failed: {type(exc).__name__}: {exc}"

    if outcome not in ("ready", "signed-out", "challenge"):
        return outcome        # it is an error message
    if outcome == "ready":
        return f"You're already signed in to {adapter.label}."
    if outcome == "challenge":
        return (f"{adapter.label} is showing a human verification check. I "
                f"don't try to get past those. The window is open - clear it "
                f"and say 'carry on'.")

    if _vault_has(adapter):
        return (
            f"You're signed out of {adapter.label}, and I have a saved login "
            f"for it. Shall I sign you in? I'll type the password straight "
            f"into the page - it never goes through speech recognition, into "
            f"this conversation, or into any log."
        )
    return (
        f"You're signed out of {adapter.label} and I don't have a login "
        f"saved for it. Do you already have an account there, or should I "
        f"open the sign-up page?"
    )


def _vault_has(adapter: SiteAdapter) -> bool:
    """Is there a saved credential for this site? Never reads the secret."""
    try:
        from . import vault
    except ImportError:
        return False
    for lister in ("list_entries", "list_credentials", "entries"):
        fn = getattr(vault, lister, None)
        if not callable(fn):
            continue
        try:
            listing = str(fn())
        except Exception:
            continue
        if adapter.key in listing.lower() or adapter.label.lower() in listing.lower():
            return True
    return False


def open_signup(agent: str = "") -> str:
    """Open the sign-up page and hand it over. Fills in nothing."""
    adapter = SITES.get((agent or "").strip().lower())
    if adapter is None:
        return "Which one - ChatGPT or Gemini?"
    url = adapter.signup_url or adapter.url
    try:
        _Session.get().do(
            lambda page: page.goto(url, timeout=45000,
                                   wait_until="domcontentloaded"),
            timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't open the sign-up page: {type(exc).__name__}: {exc}"
    return (
        f"The {adapter.label} sign-up page is open. Creating an account means "
        f"agreeing to their terms, choosing a password and usually proving "
        f"you're human, so this part is yours. Tell me when you're in and "
        f"I'll take it from there - and if you want, I'll save the login to "
        f"your vault afterwards so you never type it again."
    )


def close_browser() -> str:
    _Session.get().stop()
    return "Closed the browser I was using."


REGISTRY: dict[str, Any] = {
    "web_delegate": web_delegate,
    "web_follow_up": web_follow_up,
    "read_web_result": read_result,
    "list_web_chats": list_web_chats,
    "web_sign_in_state": web_sign_in_state,
    "open_signup": open_signup,
    "close_browser": close_browser,
}


# ---------------------------------------------------------------------------
def call(tool: str, args: dict) -> str:
    if tool == "web_delegate":
        return web_delegate(args.get("agent", ""), args.get("spec"))
    if tool == "web_follow_up":
        return web_follow_up(args.get("chat_id", ""), args.get("corrections", ""))
    if tool == "read_web_result":
        return read_result(args.get("chat_id", ""))
    if tool == "list_web_chats":
        return list_web_chats(args.get("limit", 10))
    if tool == "web_sign_in_state":
        return web_sign_in_state(args.get("agent", ""))
    if tool == "open_signup":
        return open_signup(args.get("agent", ""))
    if tool == "close_browser":
        return close_browser()
    raise KeyError(tool)
