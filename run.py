"""Jalen entrypoint.  Run:  python run.py"""
from __future__ import annotations

import argparse
import sys
import threading


def main() -> int:
    parser = argparse.ArgumentParser(description="Jalen voice assistant")
    parser.add_argument("--text", action="store_true", help="type instead of talk (no mic needed)")
    parser.add_argument("--check", action="store_true", help="run diagnostics and exit")
    parser.add_argument("--unmuted", action="store_true", help="start with voice on")
    parser.add_argument("--telegram", action="store_true", help="control Jalen from the Telegram bot instead of voice/text")
    parser.add_argument("--stop", action="store_true", help="stop the running Jalen and exit")
    parser.add_argument("--status", action="store_true", help="say whether Jalen is running, and exit")
    parser.add_argument("--restart", action="store_true", help="stop the running Jalen, then start fresh")
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
        print(f"Jalen is already running (pid {already.pid}, mode {already.mode}).")
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
    from jarvis.app import Jalen
    from jarvis.config import CONFIG
    from jarvis import tools as jarvis_tools

    jarvis = Jalen()
    if args.unmuted:
        jarvis.muted = False

    if args.text:
        # Text mode prints whatever Jarvis would have said, and that now
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
        jarvis.muted = True
        jarvis.orb.start()
        try:
            while True:
                if jarvis._quit.is_set():
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

        print("Jalen — Telegram mode. Ctrl+C to quit.\n")
        jarvis.muted = True
        jarvis.orb.start()
        try:
            asyncio.run(run_bot(jarvis, jarvis.secrets.telegram_bot_token, jarvis.secrets.telegram_allowed_user_ids))
        except KeyboardInterrupt:
            pass
        finally:
            jarvis.shutdown()
        return 0

    # The wake phrase printed here comes from config, not from a literal,
    # because the two genuinely differ today: he is Jalen, but the acoustic
    # trigger is still "hey jarvis" until the new wake model is trained. A
    # hardcoded line here would tell him to say a phrase that does not wake
    # anything — or keep saying the old one after the swap.
    wake = CONFIG.get_path("identity.wake_word", "hey jarvis").title()
    name = CONFIG.get_path("identity.name", "Jalen")
    print(f"{name} — listening for \"{wake}\".")
    print(f"  stop:     say \"{name}, quit\"  ·  Ctrl+C  ·  python run.py --stop")
    print(f"  pause:    say \"{name}, pause\" (stops listening, stays running)")
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
