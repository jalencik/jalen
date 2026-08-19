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
    args = parser.parse_args()

    if args.check:
        from scripts.check_env import main as check
        return check()

    from jarvis.app import Jarvis

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
                threading.Thread(target=jarvis.process, args=(text,), daemon=True).start()
        except KeyboardInterrupt:
            pass
        finally:
            jarvis.shutdown()
        return 0

    try:
        jarvis.run()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        jarvis.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
