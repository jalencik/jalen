"""Jalen entrypoint.  Run:  python run.py"""
from __future__ import annotations

import argparse
import sys
import threading


def _warm_what_the_first_ask_needs(mode: str) -> None:
    """
    Text and Telegram modes never call Jalen.prewarm() (only the voice loop
    does), so "what's filling my C drive" typed at a fresh start met a cold
    scan and was answered "still scanning, ask again" (live QA, 2026-10-01).
    Start the same background count voice mode starts, so it is already
    underway - and usually finished - by the time anyone asks.
    """
    if mode in ("text", "telegram"):
        from jarvis.tools import sysinfo

        sysinfo.prewarm_system_scan()


def main() -> int:
    parser = argparse.ArgumentParser(description="Jalen voice assistant")
    parser.add_argument("--text", action="store_true", help="type instead of talk (no mic needed)")
    parser.add_argument("--check", action="store_true", help="run diagnostics and exit")
    parser.add_argument("--unmuted", action="store_true", help="start with voice on")
    parser.add_argument("--telegram", action="store_true", help="control Jalen from the Telegram bot instead of voice/text")
    parser.add_argument("--stop", action="store_true", help="stop the running Jalen and exit")
    parser.add_argument("--status", action="store_true", help="say whether Jalen is running, and exit")
    parser.add_argument("--why", action="store_true", help="why did Jalen stop last time? prints the exit record and recent crashes")
    parser.add_argument("--restart", action="store_true", help="stop the running Jalen, then start fresh")
    args = parser.parse_args()

    if args.check:
        from scripts.check_env import main as check
        return check()

    # Before anything that can fail. Autostart runs this file under
    # pythonw.exe with a hidden window (start_jalen.vbs), where sys.stderr is
    # None and the interpreter throws tracebacks away rather than printing
    # them somewhere nobody looks. Installing the crash log first is what
    # makes the difference between a process that vanishes and one that
    # leaves a traceback in data/crash.log. See jarvis/crashlog.py.
    from jarvis import crashlog
    crashlog.install()

    from jarvis import runtime

    if args.status:
        print(runtime.status())
        return 0

    if args.why:
        return _why()

    if args.stop:
        print(runtime.stop_running_instance())
        return 0

    if args.restart:
        print(runtime.stop_running_instance())

    # Single instance, always. A second launch would otherwise open a second
    # microphone listener, a second Telegram poller and a second TTS output
    # on the same devices — which is what "I saw two Jalen instances" and
    # the doubled/competing replies actually were.
    mode = "telegram" if args.telegram else "text" if args.text else "voice"
    already = runtime.acquire(mode)
    if already is not None:
        print(f"Jalen is already running (pid {already.pid}, mode {already.mode}).")
        print("  stop it:     python run.py --stop")
        print("  restart it:  python run.py --restart")
        return 1

    try:
        return _serve(args)
    except BaseException as exc:
        # Recorded here rather than left to the excepthook, because the
        # excepthook does not fire for everything that ends a process and
        # this is the one place that sees every failure _serve() can produce.
        # Re-raised afterwards so behaviour is unchanged — the only new thing
        # is that the reason now exists on disk.
        if not isinstance(exc, (KeyboardInterrupt, SystemExit)):
            crashlog.record_exception("run.py", exc)
            crashlog.note_exit(
                "unhandled-exception", error=f"{type(exc).__name__}: {exc}"[:400]
            )
        raise
    finally:
        # Every exit path — clean quit, Ctrl+C, or an unhandled error — must
        # drop the lock, or the next launch refuses to start.
        runtime.release()


def _why() -> int:
    """
    `python run.py --why` — the answer to "it stopped on its own again".

    Two things, in the order you want them: how the last run ended, then the
    tail of the crash log. Before this the honest answer to that question was
    "nothing was recorded", which is how the 21 August exit stayed a mystery.
    """
    from jarvis import crashlog

    record = crashlog.previous_exit()
    if record is None:
        print("No exit record yet. It is written from the next start onward.")
    elif record.get("state") == "running":
        print("The last run ended WITHOUT shutting down.")
        print(f"  session   {record.get('session_id', '?')}")
        print(f"  pid       {record.get('pid', '?')}")
        print(f"  mode      {record.get('mode', '?')}")
        print(f"  started   {record.get('started_iso', '?')}")
        print("\nThat means it was killed, or it died somewhere it could not report.")
    else:
        print(f"The last run stopped cleanly: {record.get('reason', '?')}")
        print(f"  session   {record.get('session_id', '?')}")
        print(f"  ended     {record.get('ended_iso', '?')}")
        if record.get("uptime_s") is not None:
            print(f"  ran for   {record['uptime_s']}s")
        if record.get("error"):
            print(f"  error     {record['error']}")

    if crashlog.CRASH_LOG.exists():
        lines = crashlog.CRASH_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = lines[-40:]
        print(f"\n--- {crashlog.CRASH_LOG} (last {len(tail)} of {len(lines)} lines) ---")
        for line in tail:
            print(line)
    else:
        print(f"\nNo crash log at {crashlog.CRASH_LOG}: nothing has crashed since this was added.")
    return 0


def _serve(args) -> int:
    from jarvis.app import Jalen
    from jarvis.config import CONFIG
    from jarvis import tools as jalen_tools

    mode = "telegram" if args.telegram else "text" if args.text else "voice"
    jalen = Jalen(mode=mode)
    if args.unmuted:
        jalen.muted = False
    _warm_what_the_first_ask_needs(mode)

    if args.text:
        # Text mode prints whatever Jalen would have said, and that now
        # includes email and Telegram bodies written by other people. A
        # Windows console defaults to a legacy code page (cp1251 on this
        # machine), and print() RAISES on a character it cannot encode
        # rather than degrading — so one narrow no-break space in a Google
        # email, a character you cannot even see, took the whole session
        # down. Reconfigure stdout to UTF-8 and replace anything still
        # unencodable instead of dying.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass  # not a real console (piped, redirected); nothing to fix
        print("Jalen — text mode. Ctrl+C to quit.\n")
        jalen.muted = True
        jalen.orb.start()
        try:
            while True:
                if jalen._quit.is_set():
                    break
                try:
                    text = input("you > ").strip()
                except EOFError:
                    # No terminal attached (piped/background). Text mode is
                    # interactive by definition — exit cleanly instead of
                    # spinning on a stream that will never produce input.
                    break
                if text.lower() in ("quit", "exit"):
                    break
                if not text:
                    continue
                jalen.say = lambda t, force=False: print(f"jalen > {t}\n")  # type: ignore
                jalen.say_blocking = lambda t: print(f"jalen > {t}\n")      # type: ignore
                # Dispatch on its own thread rather than calling inline: a
                # RED confirmation or AMBER stop-window makes jalen.process()
                # block until it's answered, and the answer is itself a later
                # call to jalen.process() (see app.py's _awaiting_confirmation
                # handling). Calling it inline here would mean input() can
                # never be reached again to type that answer — every
                # confirmation would just time out. See HANDOFFPROMPT §3/§7.
                #
                # com_initialized(): a fresh thread has no COM apartment, so
                # any UIA-based tool (focus_window, window_state, and Phase C's
                # desktop.py) fails with "CoInitialize has not been called" —
                # not a crash, just silently never doing what was asked. See
                # jalen/tools/__init__.py's com_initialized() docstring.
                def _run_turn(t=text):
                    with jalen_tools.com_initialized():
                        jalen.process(t)

                threading.Thread(target=_run_turn, daemon=True).start()
        except KeyboardInterrupt:
            jalen._exit_reason = "keyboard-interrupt"
        finally:
            jalen.shutdown("text-mode-ended")
        return 0

    if args.telegram:
        if not jalen.secrets.telegram_bot_token:
            print("TELEGRAM_BOT_TOKEN isn't set in .env — see CREDENTIALS.md.")
            jalen.shutdown("missing-telegram-token")
            return 1
        if not jalen.secrets.telegram_allowed_user_ids:
            print(
                "TELEGRAM_ALLOWED_USER_IDS is empty — the bot would refuse everyone. "
                "Add your numeric Telegram ID (from @userinfobot) to .env first."
            )
            jalen.shutdown("no-allowed-telegram-users")
            return 1

        import asyncio

        from jarvis.integrations.telegram_bot import run_bot

        print("Jalen — Telegram mode. Ctrl+C to quit.\n")
        jalen.muted = True
        jalen.orb.start()
        try:
            asyncio.run(run_bot(jalen, jalen.secrets.telegram_bot_token, jalen.secrets.telegram_allowed_user_ids))
        except KeyboardInterrupt:
            jalen._exit_reason = "keyboard-interrupt"
        finally:
            jalen.shutdown("telegram-mode-ended")
        return 0

    # The wake phrase printed here comes from config, not from a literal,
    # because the two genuinely differ today: he is Jalen, but the acoustic
    # trigger is still "hey jalen" until the new wake model is trained. A
    # hardcoded line here would tell him to say a phrase that does not wake
    # anything — or keep saying the old one after the swap.
    wake = CONFIG.get_path("identity.wake_word", "hey jalen").title()
    name = CONFIG.get_path("identity.name", "Jalen")
    print(f"{name} — listening for \"{wake}\".")
    print(f"  stop:     say \"{name}, quit\"  ·  Ctrl+C  ·  python run.py --stop")
    print(f"  pause:    say \"{name}, pause\" (stops listening, stays running)")
    print("  status:   python run.py --status\n")
    try:
        jalen.run()
    except KeyboardInterrupt:
        jalen._exit_reason = "keyboard-interrupt"
        print("\nStopping…")
    finally:
        # No reason passed: shutdown() falls back to the one run() recorded
        # when its loop ended, which is more specific than anything this
        # caller knows.
        jalen.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
