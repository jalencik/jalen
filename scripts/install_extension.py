"""
Register Jalen's native-messaging host with Chrome. Run once, from the MAIN
checkout (it needs .venv there), before or after loading the extension:

    .venv\\Scripts\\python.exe scripts\\install_extension.py

No extension ID is needed: the "key" in browser_extension/manifest.json pins
it. Pass one only if Chrome shows a different ID for the extension.

Writes jalen_bridge_host.bat and native_host_manifest.json in this folder and
registers them for the CURRENT user only
(HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\com.jalen.bridge), with
no admin rights. The launcher always starts the bridge from this folder, so
Jalen must run from this same folder for the extension to connect.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST_NAME = "com.jalen.bridge"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
LAUNCHER = ROOT / "jalen_bridge_host.bat"
HOST_MANIFEST = ROOT / "native_host_manifest.json"

# The extension's id is PINNED by the "key" in browser_extension/manifest.json,
# so it is always this - which means the user does not have to load the
# extension, read its id, and paste it back. He just runs this. A stable id
# is also what stops the single most common "not connected" cause: a host
# manifest whose allowed_origins names an id the extension no longer has.
PINNED_EXTENSION_ID = "emlfljokadcfdfedhkgkgecpgjfbhfkg"


def _write_launcher() -> None:
    """
    A .bat, because Chrome's native-host `path` must be an executable and a
    .py is not one.

    IT MUST cd TO THE PROJECT ROOT FIRST. Chrome launches the host from its
    OWN working directory, not the project's, so `python -m jarvis.bridge.
    native_host` from there fails with "No module named jarvis" - the host
    dies on the first byte, the port disconnects, and the extension shows
    "not connected" forever. Measured: this was exactly that bug. `cd /d`
    handles a project on a different drive letter than Chrome.

    @echo off, and nothing else writes to stdout, so no stray bytes corrupt
    the native-messaging frames.
    """
    LAUNCHER.write_text(
        "@echo off\r\n"
        f'cd /d "{ROOT}"\r\n'
        f'"{VENV_PY}" -m jarvis.bridge.native_host\r\n',
        encoding="utf-8",
    )


def _write_manifest(extension_id: str) -> None:
    manifest = {
        "name": HOST_NAME,
        "description": "Jalen browser bridge",
        "path": str(LAUNCHER),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }
    HOST_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _register() -> str:
    """
    Point Chrome at the manifest via HKCU. Returns the key path written.

    Current-user hive on purpose: no admin, and it cannot affect anyone else
    on the machine. Chrome reads the default value of this key to find the
    host manifest.
    """
    import winreg

    key_path = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(HOST_MANIFEST))
    return rf"HKCU\{key_path}"


def main(argv) -> int:
    # Without this, "--help" was taken as an extension id and refused.
    if len(argv) > 1 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    # The id is pinned, so no argument is needed. One is still accepted, for
    # the rare case the extension was loaded without the key (e.g. packed
    # differently) and shows a different id.
    extension_id = argv[1].strip().strip("/") if len(argv) > 1 and argv[1].strip() \
        else PINNED_EXTENSION_ID
    if not extension_id.isalnum() or len(extension_id) != 32:
        print(f"That doesn't look like a Chrome extension id "
              f"(32 letters): {extension_id!r}")
        return 2

    if not VENV_PY.is_file():
        print(f"Can't find the venv python at {VENV_PY}. Run from the project "
              f"root with the venv created.")
        return 1

    _write_launcher()
    _write_manifest(extension_id)
    try:
        key = _register()
    except Exception as exc:  # noqa: BLE001  - winreg only exists on Windows
        print(f"Wrote the manifest but couldn't register with Chrome: "
              f"{type(exc).__name__}: {exc}")
        print("On non-Windows, register the host manifest per your OS's "
              "Chrome native-messaging location.")
        return 1

    print("Done. Jalen's browser bridge is registered.")
    print(f"  launcher : {LAUNCHER}")
    print(f"  manifest : {HOST_MANIFEST}")
    print(f"  registry : {key}")
    print("Now: fully restart Chrome, start Jalen, and open the extension "
          "popup - it should say 'connected'.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
