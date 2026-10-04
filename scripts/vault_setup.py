r"""
Create or update the credentials vault. Run once:

    .venv\Scripts\python.exe scripts\vault_setup.py

Asks for a passphrase and the secrets, encrypts them, and writes
data/vault.json. The passphrase is NEVER stored — not in the file, not in
.env, not in config. If it were, it would just be a second copy of the thing
it protects, and the vault would be decoration.

Everything is typed with getpass, so nothing appears on screen and nothing
lands in the shell history.

It is also where he says WHICH SITE each secret is for. Jalen types a secret
only on the site(s) it is tied to (jalen/tools/vault.py, "which site a
secret is for"), and this is the one place a tie is added or changed on
purpose - no tool can do it, because a tool the model could call would let a
page he was reading move his Google password to itself. The sites are typed
here with input(), not getpass: they are not secret.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jalen.tools.vault import (  # noqa: E402
    VAULT_PATH,
    _load_blob,
    _open,
    _save_blob,
    _seal,
    describe_secret_sites,
    forget_secret_sites_except,
    tie_secret,
)

# The ones he actually named. Anything else can be added by typing a name.
SUGGESTED = [
    ("sign_in_code", "the code you use to sign in to sites"),
    ("phone", "your phone number, for application forms"),
    ("email", "the email address you apply with"),
]


def tie_to_sites(names: list[str]) -> None:
    """
    Which site each secret is for. Enter keeps what is there - so running
    this again to add one secret does not ask him to retype every site.
    """
    print()
    print("  Which site is each one for? Jalen will only ever type it there.")
    print("  Type the site (accounts.google.com), several with commas, or *")
    print("  if it goes into forms anywhere, like a phone number. Enter leaves")
    print("  it as it is; one not tied yet is tied to the first site you say")
    print("  yes to using it on.")
    for name in names:
        answer = input(f"  {name} [{describe_secret_sites(name)}]: ").strip()
        if not answer:
            continue
        sites = [part.strip() for part in answer.replace(";", ",").split(",")
                 if part.strip()]
        if tie_secret(name, sites, how="vault_setup"):
            print(f"    {name}: {describe_secret_sites(name)}")
        else:
            print(f"    I couldn't read that as a site, so {name} is as it was.")


def main() -> int:
    print()
    print("  Jalen credentials vault")
    print("  " + "-" * 44)
    print("  Stored encrypted in data/vault.json.")
    print("  The passphrase is never written anywhere — if you forget it,")
    print("  the vault cannot be recovered and you make a new one.")
    print()

    existing_blob = _load_blob()
    secrets: dict[str, str] = {}

    if existing_blob is not None:
        print("  A vault already exists. Enter its passphrase to add to it,")
        print("  or press Enter on a blank line to start over.")
        passphrase = getpass.getpass("  Existing passphrase: ")
        if passphrase:
            opened = _open(existing_blob, passphrase)
            if opened is None:
                print("\n  That didn't unlock it. Nothing was changed.")
                return 1
            secrets = opened
            print(f"  Unlocked — {len(secrets)} existing entries.\n")
        else:
            print("  Starting a fresh vault.\n")
            passphrase = ""

    if not secrets:
        passphrase = getpass.getpass("  Choose a passphrase: ")
        if len(passphrase) < 8:
            print("\n  Too short — use at least 8 characters. Nothing was written.")
            return 1
        if passphrase != getpass.getpass("  Type it again: "):
            print("\n  Those didn't match. Nothing was written.")
            return 1
        print()

    print("  Leave a value blank to skip it.\n")
    for name, description in SUGGESTED:
        current = " (already set)" if name in secrets else ""
        value = getpass.getpass(f"  {description}{current}: ")
        if value:
            secrets[name] = value

    while True:
        extra = input("\n  Another secret? Name it, or press Enter to finish: ").strip()
        if not extra:
            break
        value = getpass.getpass(f"  Value for {extra}: ")
        if value:
            secrets[extra] = value

    if not secrets:
        print("\n  Nothing to store. No vault written.")
        return 1

    _save_blob(_seal(secrets, passphrase))
    print(f"\n  Saved {len(secrets)} entries to {VAULT_PATH}")
    # After the save, so nothing below can cost him the vault itself.
    forget_secret_sites_except(secrets)
    tie_to_sites(sorted(secrets))
    print()
    print("  Say \"unlock the vault\" and give Jalen the passphrase when he asks.")
    print()
    print("  Jalen will still ask before typing anything into a site he has")
    print("  not seen before, and you can answer once or always.")
    return 0


if __name__ == "__main__":
    # main() ignores its arguments, so without this "--help" went straight to
    # the passphrase prompt instead of printing help.
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
        print(__doc__)
        raise SystemExit(0)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  Cancelled. Nothing was written.")
        sys.exit(1)
