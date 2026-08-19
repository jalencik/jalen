"""Jarvis entrypoint.  Run:  python run.py"""
from __future__ import annotations

import argparse
import sys
import threading


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis voice assistant")
    parser.add_argument("--text", action="store_true", help="type instead of talk (no mic needed)")
    parser.add_argument("--check", action="store_true", help="run diagnostics and exit")
    parser.add_argument("--unmuted", action="store_true", help="start with voice on")
    parser.add_argument("--telegram", action="store_true", help="control Jarvis from the Telegram bot instead of voice/text")
    parser.add_argument("--stop", action="store_true", help="stop the running Jarvis and exit")
    parser.add_argument("--status", action="store_true", help="say whether Jarvis is running, and exit")
    parser.add_argument("--restart", action="store_true", help="stop the running Jarvis, then start fresh")
    args = parser.parse_args()

    if args.check:
        from scripts.check_env import main as check
        return check()

    from jarvis import runtime

    if args.status:
        print(runtime.status())
        return 0

    if args.stop:
        print(runtime.stop_running_instance())
        return 0

    if args.restart:
        print(runtime.stop_running_instance())

    # Single instance, always. A second launch would otherwise open a second
    # microphone listener, a second Telegram poller and a second TTS output
    # on the same devices — which is what "I saw two Jarvis instances" and
    # the doubled/competing replies actually were.
    mode = "telegram" if args.telegram else "text" if args.text else "voice"
    already = runtime.acquire(mode)
    if already is not None:
        print(f"Jarvis is already running (pid {already.pid}, mode {already.mode}).")
        print("  stop it:     python run.py --stop")
        print("  restart it:  python run.py --restart")
        return 1

    try:
        return _serve(args)
    finally:
        # Every exit path — clean quit, Ctrl+C, or an unhandled error — must
        # drop the lock, or the next launch refuses to start.
        runtime.release()


def _serve(args) -> int:
    from jarvis.app import Jarvis
    from jarvis import tools as jarvis_tools

    jarvis = Jarvis()
    if args.unmuted:
        jarvis.muted = False

    if args.text:
        print("Jarvis — text mode. Ctrl+C to quit.\n")
        jarvis.muted = True
        jarvis.orb.start()
        try:
            while True:
                text = input("you > ").strip()
                if text.lower() in ("quit", "exit"):
                    break
                if not text:
                    continue
                jarvis.say = lambda t, force=False: print(f"jarvis > {t}\n")  # type: ignore
                jarvis.say_blocking = lambda t: print(f"jarvis > {t}\n")      # type: ignore
                # Dispatch on its own thread rather than calling inline: a
                # RED confirmation or AMBER stop-window makes jarvis.process()
                # block until it's answered, and the answer is itself a later
                # call to jarvis.process() (see app.py's _awaiting_confirmation
                # handling). Calling it inline here would mean input() can
                # never be reached again to type that answer — every
                # confirmation would just time out. See HANDOFFPROMPT §3/§7.
                #
                # com_initialized(): a fresh thread has no COM apartment, so
                # any UIA-based tool (focus_window, window_state, and Phase C's
                # desktop.py) fails with "CoInitialize has not been called" —
                # not a crash, just silently never doing what was asked. See
                # jarvis/tools/__init__.py's com_initialized() docstring.
                def _run_turn(t=text):
                    with jarvis_tools.com_initialized():
                        jarvis.process(t)

                threading.Thread(target=_run_turn, daemon=True).start()
        except KeyboardInterrupt:
            pass
        finally:
            jarvis.shutdown()
        return 0

    if args.telegram:
        if not jarvis.secrets.telegram_bot_token:
            print("TELEGRAM_BOT_TOKEN isn't set in .env — see CREDENTIALS.md.")
            jarvis.shutdown()
            return 1
        if not jarvis.secrets.telegram_allowed_user_ids:
            print(
                "TELEGRAM_ALLOWED_USER_IDS is empty — the bot would refuse everyone. "
                "Add your numeric Telegram ID (from @userinfobot) to .env first."
            )
            jarvis.shutdown()
            return 1

        import asyncio

        from jarvis.integrations.telegram_bot import run_bot

        print("Jarvis — Telegram mode. Ctrl+C to quit.\n")
        jarvis.muted = True
        jarvis.orb.start()
        try:
            asyncio.run(run_bot(jarvis, jarvis.secrets.telegram_bot_token, jarvis.secrets.telegram_allowed_user_ids))
        except KeyboardInterrupt:
            pass
        finally:
            jarvis.shutdown()
        return 0

    print("Jarvis — listening for \"Hey Jarvis\".")
    print("  stop:     say \"Jarvis, quit\"  ·  Ctrl+C  ·  python run.py --stop")
    print("  pause:    say \"Jarvis, pause\" (stops listening, stays running)")
    print("  status:   python run.py --status\n")
    try:
        jarvis.run()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        jarvis.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
