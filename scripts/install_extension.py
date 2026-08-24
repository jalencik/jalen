"""
Register Jalen's native-messaging host with Chrome. Run once, after loading
the unpacked extension.

WHY AN EXTENSION ID IS NEEDED
-----------------------------
Chrome only lets a native host talk to extensions named in its manifest's
allowed_origins, and an unpacked extension's id is assigned by Chrome when
you load it. So the order is: load the extension (chrome://extensions,
Developer mode, Load unpacked -> browser_extension/), copy the Extension ID
it shows, then:

    .venv\\Scripts\\python.exe scripts\\install_extension.py <EXTENSION_ID>

This writes the host manifest and the launcher, and registers both with
Chrome for the CURRENT user only (HKCU) - never machine-wide, so it needs no
admin rights and touches nothing outside this account.
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


def _write_launcher() -> None:
    """
    A .bat, because Chrome's native-host `path` must be an executable and a
    .py is not one. It runs the venv's python on the host module, and forwards
    Chrome's stdio untouched. @echo off so no stray bytes reach the pipe.
    """
    LAUNCHER.write_text(
        "@echo off\r\n"
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
    if len(argv) < 2 or not argv[1].strip():
        print("Give me the extension's ID. Load browser_extension/ as an "
              "unpacked extension first (chrome://extensions -> Developer "
              "mode -> Load unpacked), copy the Extension ID it shows, then:")
        print("  .venv\\Scripts\\python.exe scripts\\install_extension.py "
              "<EXTENSION_ID>")
        return 2

    extension_id = argv[1].strip().strip("/")
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
