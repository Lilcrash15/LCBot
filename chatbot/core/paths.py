"""Where LCBot's own files live -- always next to the running app
itself, not wherever the OS happened to set the current working
directory when it was launched.

For a plain `python run_bot.py` run, `app_dir()` is the same as
`os.getcwd()` almost all of the time, so nothing changes there. It
matters for the compiled exe: double-clicking TwitchChatBotV2.exe
directly inside its own folder in Explorer sets the working directory
to that folder, but that's not guaranteed -- a desktop shortcut with
its own "Start in" field (blank or pointing elsewhere), launching via
Task Scheduler/an autostart entry, or a launcher script can all hand
the process a completely different working directory while the exe
itself still runs fine from wherever it actually sits. Everything
LCBot reads or writes next to itself -- config.json, chatbot.db,
assets/icon.ico, overlay/, emote_cache/, song_cache/ -- should resolve
relative to the exe's real location regardless of how it was launched.
PyInstaller sets `sys.frozen = True` and `sys.executable` to the real
compiled exe's path in that case, which `os.getcwd()` can't guarantee.
"""
from __future__ import annotations

import os
import sys


def app_dir() -> str:
    """Directory the app should treat as "next to itself" for reading
    or writing its own files. The frozen-exe branch is the one real
    fix here; the source-run branch is unchanged (still os.getcwd(),
    same as every call site already assumed before this existed)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.getcwd()
