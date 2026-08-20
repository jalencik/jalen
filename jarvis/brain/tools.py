"""
Wraps jarvis.tools.REGISTRY (system.py + desktop.py + filesystem.py, every
tool Jarvis owns) as Claude Agent SDK tools, so the brain can actually use
them — the handoff's "no empty tools=[]".

One data-driven table rather than 31 hand-written @tool blocks: keeping
that many blocks in sync with jarvis.tools.REGISTRY by hand is exactly the
kind of thing that drifts silently. build_sdk_tools() asserts the table and
the registry match at call time, before Jarvis ever starts talking to
Claude — a tool present in one but not the other is a startup error, not a
runtime surprise discovered mid-conversation.

Every wrapped call runs jarvis.tools.call() in a worker thread (via
asyncio.to_thread, so a slow tool — a subprocess, a UIA wait — never blocks
Jarvis's single persistent event loop) inside tools.com_initialized():
UIA/COM-based tools (desktop.py, and system.py's focus_window/
window_state/volume_set) raise "CoInitialize has not been called" on any
thread that hasn't had COM explicitly initialized, and asyncio.to_thread's
worker threads never have been. Confirmed live before this was added:
every one of those tools failed silently (a caught exception, not a crash,
but never doing what was asked) when called this way without it.
"""
from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import tool as sdk_tool

from .. import tools as jarvis_tools

# name: (description, {param: (json_type, description, required)})
TOOL_SPECS: dict[str, tuple[str, dict[str, tuple[str, str, bool]]]] = {
    # ---- system.py: media / windows / facts ------------------------------
    "media_play_pause": ("Play or pause whatever media player currently has focus.", {}),
    "media_next": ("Skip to the next track.", {}),
    "media_previous": ("Go back to the previous track.", {}),
    "volume_step": ("Nudge system volume up or down a few steps.", {
        "direction": ("string", "'up' or 'down'", True),
    }),
    "volume_set": ("Set system volume to an absolute percentage.", {
        "level": ("integer", "0-100", True),
    }),
    "volume_mute_toggle": ("Mute or unmute system audio output.", {
        "mute": ("boolean", "true to mute, false to unmute", True),
    }),
    "open_app": ("Launch an application by name.", {
        "name": ("string", "app name, e.g. 'chrome', 'notepad', 'vs code'", True),
    }),
    "close_app": ("Close a running app's window by (partial) title. Can lose unsaved work.", {
        "name": ("string", "substring of the window title", True),
    }),
    "open_folder": ("Open a folder in File Explorer.", {
        "path": ("string", "folder path", True),
    }),
    "open_url": ("Open a URL in the default browser.", {
        "url": ("string", "the URL to open (a bare domain gets https:// prepended)", True),
    }),
    "focus_window": ("Bring a window to the foreground by (partial) title.", {
        "name": ("string", "substring of the window title", True),
    }),
    "window_state": ("Minimise or maximise the current foreground window.", {
        "state": ("string", "'minimize' or 'maximize'", True),
    }),
    "lock_workstation": ("Lock the screen.", {}),
    "sign_out": ("Log the current Windows user out. Closes every running app.", {}),
    "empty_recycle_bin": ("Permanently empty the Recycle Bin. Irreversible.", {}),
    "get_time": ("Current time.", {}),
    "get_date": ("Current date.", {}),
    "get_battery": ("Battery level, if this machine has one.", {}),
    "get_system_status": ("CPU, memory and disk usage.", {}),
    "screenshot": ("Take a screenshot and save it to disk.", {
        "path": ("string", "optional destination path; a sensible default is used if omitted", False),
    }),
    # ---- desktop.py --------------------------------------------------------
    "get_window_list": ("List currently visible top-level windows.", {}),
    "read_screen": (
        "Read a window's UI as text via the accessibility tree — far cheaper "
        "and more reliable than a screenshot for reading what's on screen. "
        "Note: Chrome and other Chromium/Electron apps (e.g. VS Code) often "
        "expose almost nothing this way unless launched with special flags — "
        "the result says so honestly when that happens.",
        {"window": ("string", "substring of the window title; omit for the current foreground window", False)},
    ),
    "click_element": ("Click a labelled control inside a window.", {
        "window": ("string", "substring of the window title", True),
        "name": ("string", "substring of the control's accessible name", True),
        "control_type": ("string", "optional UIA control type, e.g. 'Button', 'MenuItem'", False),
    }),
    "type_text": ("Type text into whatever control is focused in a window.", {
        "window": ("string", "substring of the window title", True),
        "text": ("string", "text to type", True),
    }),
    "keyboard_shortcut": ("Send a keyboard shortcut.", {
        "keys": ("string", "uiautomation SendKeys syntax, e.g. '{Ctrl}s'", True),
        "window": ("string", "window to activate first; omit for whatever's already focused", False),
    }),
    # ---- filesystem.py ------------------------------------------------------
    "read_file": ("Read a text file's contents.", {
        "path": ("string", "file path", True),
    }),
    "list_directory": ("List a folder's contents.", {
        "path": ("string", "folder path", True),
    }),
    "search_files": ("Find files by (partial) name.", {
        "query": ("string", "substring to search for in filenames", True),
        "root": ("string", "optional folder to search under; defaults to the configured index paths", False),
    }),
    "create_file": ("Create a new text file. Fails if it already exists — use edit_file to change one.", {
        "path": ("string", "file path", True),
        "content": ("string", "file contents", False),
    }),
    "edit_file": ("Overwrite an existing file's contents.", {
        "path": ("string", "file path", True),
        "content": ("string", "new file contents", True),
    }),
    "create_folder": ("Create a folder, and any missing parent folders.", {
        "path": ("string", "folder path", True),
    }),
    "copy_file": ("Copy a file to a destination path or folder.", {
        "path": ("string", "source file path", True),
        "destination": ("string", "destination path or folder", True),
    }),
    "move_file": ("Move a file to a destination path or folder.", {
        "path": ("string", "source file path", True),
        "destination": ("string", "destination path or folder", True),
    }),
    "rename_file": ("Rename a file in place.", {
        "path": ("string", "file path", True),
        "new_name": ("string", "new filename, not a full path", True),
    }),
    "delete_file": ("Delete a file. Irreversible — always requires spoken confirmation first.", {
        "path": ("string", "file path", True),
    }),
    # ---- launcher.py --------------------------------------------------------
    "open_target": (
        "Open exactly ONE thing by name: an application, a file, or a "
        "folder. Handles approximate names, nicknames and misspellings, and "
        "resolves the app actually installed on this machine (e.g. "
        "'Telegram' opens AyuGram here). Prefer this over open_app/"
        "open_folder for any 'open X' request. IMPORTANT: it takes a single "
        "target and cannot launch an app already pointed at a file or "
        "folder — there is no tool anywhere in this set that opens an app "
        "with a startup argument or working directory (e.g. 'open VS Code "
        "in the eco pulse folder', 'open Photoshop with image.png'). For "
        "that phrasing, say plainly that you can't launch the app already "
        "pointed at that location, then offer the closest real options "
        "(open the app, or open the file/folder, as two separate actions) "
        "— never silently call this with only the folder/file name and "
        "drop the app half of the request as if the whole thing was done.",
        {"name": ("string", "what to open: app name, file name, or full path", True)},
    ),
    "open_in": (
        "Launch an application already pointed at a file or folder, e.g. VS Code opened "
        "on a project folder, or Excel opened with a spreadsheet. Use this whenever a "
        "request names BOTH an app and a place - never call open_target with just one half.",
        {"app": ("string", "the application name", True),
         "target": ("string", "the file or folder to open it at", True)},
    ),
    "search_site": (
        "Search a specific website and show the results: YouTube, Google, GitHub, Reddit, "
        "Amazon, Wikipedia, Spotify, Maps and others. Use for any 'search X for Y' or "
        "'look up Y on X' request.",
        {"site": ("string", "the website, e.g. youtube", True),
         "query": ("string", "what to search for", True)},
    ),
    "play_on_youtube": (
        "Search YouTube and start the first result playing. Use for 'play X', "
        "'go to youtube and play X', or any request to hear/watch something on YouTube.",
        {"query": ("string", "song, video or channel to play", True)},
    ),
    "remember_alias": (
        "Teach a nickname for an app, file or folder so it can be opened by "
        "that name later. Persists across restarts.",
        {"name": ("string", "the nickname to remember", True),
         "target": ("string", "full path, or the app name it should mean", True)},
    ),
    "list_aliases": ("List every nickname taught so far.", {}),
    "refresh_system_scan": (
        "Force a fresh disk/cleanup scan in the background. Use only when the user says the "
        "figures look out of date; normal reports already answer from a recent scan.",
        {},
    ),
    # ---- memory.py ----------------------------------------------------------
    "remember": ("Store a fact or preference for later recall. Refuses anything that looks like a credential.", {
        "text": ("string", "what to remember", True),
    }),
    "recall_memory": ("Search remembered facts and preferences by meaning, not exact wording.", {
        "query": ("string", "what to recall", True),
    }),
    # ---- sysinfo.py -----------------------------------------------------------
    "disk_report": (
        "Report free space per drive, plus the biggest folders and files in the "
        "user's Desktop, Documents, Downloads and Temp folders. Read-only.",
        {},
    ),
    "cleanup_suggestions": (
        "Report specifically safe-to-delete things with real measured sizes: Temp "
        "files, old Downloads, recycle bin, browser/pip/npm caches. Only suggests "
        "— never deletes anything; use delete_file for that after confirming.",
        {"downloads_older_than_days": (
            "integer", "how old a Downloads file must be to count as stale; defaults to 30", False,
        )},
    ),
    "memory_report": ("Report overall RAM usage and the top processes by memory use.", {
        "top_n": ("integer", "how many top processes to list; defaults to 5", False),
    }),
    # ---- documents.py -----------------------------------------------------
    "read_document": (
        "Extract readable text from a document: plain text, markdown, csv, "
        "json, code/config files, .docx/.pptx/.xlsx (read directly as the "
        "zip-of-XML they are), and .pdf (real text extraction, page by "
        "page). Call this directly for a PDF — do not assume PDFs are "
        "unsupported and skip the call. The two things it genuinely can't "
        "read: old binary Office formats (.doc/.xls/.ppt — pre-2007, not "
        "the same as .docx/.xlsx/.pptx), and a scanned/image-only PDF with "
        "no text layer (would need OCR, which isn't installed). Both cases "
        "return a clear message rather than empty text, so trust what it "
        "reports back instead of pre-deciding a file won't work.",
        {"path": ("string", "file path", True)},
    ),
    "summarize_document": (
        "Extract a document's text, clearly labelled with its name/path/type, "
        "so it can be summarised. Same format support as read_document — "
        "including .pdf — does not attempt to summarise anything itself.",
        {"path": ("string", "file path", True)},
    ),
    "search_in_files": (
        "Find files whose CONTENTS contain a phrase, not just the filename. "
        "Only reads formats read_document supports, bounded on time/file-count/"
        "match-count so it can't hang on a large folder.",
        {"query": ("string", "text to search for inside file contents", True),
         "folder": ("string", "optional folder to search under; defaults to the configured index paths", False)},
    ),
}


def _schema(params: dict[str, tuple[str, str, bool]]) -> dict:
    properties = {name: {"type": t, "description": desc} for name, (t, desc, _req) in params.items()}
    required = [name for name, (_t, _d, req) in params.items() if req]
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _call_with_com(name: str, args: dict) -> str:
    with jarvis_tools.com_initialized():
        return jarvis_tools.call(name, args)


def _make_wrapper(name: str):
    async def wrapper(args: dict) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(_call_with_com, name, args or {})
            return {"content": [{"type": "text", "text": str(result)}]}
        except KeyError:
            return {
                "content": [{"type": "text", "text": f"{name} isn't implemented yet."}],
                "is_error": True,
            }
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"{name} failed: {type(exc).__name__}: {exc}"}],
                "is_error": True,
            }

    wrapper.__name__ = f"jarvis_tool_{name}"
    return wrapper


def build_sdk_tools() -> list:
    """
    Every jarvis.tools.REGISTRY function, wrapped for the Agent SDK.
    Asserted 1:1 against the registry so a tool added to one side and
    forgotten on the other fails loudly at startup.
    """
    registry_names = set(jarvis_tools.REGISTRY)
    spec_names = set(TOOL_SPECS)
    missing_specs = registry_names - spec_names
    stale_specs = spec_names - registry_names
    if missing_specs or stale_specs:
        raise RuntimeError(
            "jarvis.tools.REGISTRY and brain/tools.py TOOL_SPECS have drifted: "
            f"missing specs for {missing_specs or None}, stale specs for {stale_specs or None}"
        )

    return [
        sdk_tool(name, description, _schema(params))(_make_wrapper(name))
        for name, (description, params) in TOOL_SPECS.items()
    ]
