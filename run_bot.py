#!/usr/bin/env python3
"""Entry point -- launches the Twitch Chat Bot desktop app.

Usage:
    python run_bot.py

First run creates config.json (connection settings) and chatbot.db
(commands/points/quotes/etc.) next to this script. Fill in Settings
from the GUI before hitting Connect -- see README.md for how to get a
Twitch Dev Console app and OAuth tokens.
"""
import logging
import logging.handlers
import os
import sys


def _set_windows_app_id() -> None:
    """Gives this process a stable Application User Model ID (AUMID)
    before any window is created -- must happen this early, or not at
    all, per Microsoft's own docs on SetCurrentProcessExplicitAppUser-
    ModelID. Without an explicit one, Windows derives an AUMID from the
    process on its own, and that derivation is exactly what caused a
    live, reported symptom (2026-09-04): the taskbar icon showed
    correctly on one launch, then reverted to a generic icon on a
    later one, with no code change in between. Two things point at
    Windows' own icon/taskbar-identity caching rather than a bug in
    _apply_app_icon() itself: (1) `build_exe.bat` builds with
    PyInstaller's `--onefile`, which re-extracts the app to a fresh
    random temp folder on every single launch -- a different path
    each time gives Windows a moving target to key any icon/identity
    cache off of; (2) this is a well-documented PyInstaller/Tkinter-
    on-Windows fix, not a guess -- explicitly setting an AUMID is the
    standard workaround precisely because it gives Windows one stable
    identity to hang the taskbar icon (and grouping) off, independent
    of whichever temp path or icon-cache state a given launch happens
    to have. Safe to call on any platform: a no-op (not an exception)
    anywhere `ctypes.windll` doesn't exist, i.e. everywhere but
    Windows."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LCBot.TwitchChatBotV2")
    except (AttributeError, OSError):
        pass  # cosmetic (taskbar identity/grouping) only -- never worth blocking startup over


def main() -> None:
    _set_windows_app_id()
    handlers: list = [logging.StreamHandler()]
    try:
        # The compiled exe is built --windowed (no console window), so
        # a plain StreamHandler's output goes nowhere anyone can see --
        # this is the only place any warning/error logged anywhere in
        # the app (a missing icon file, an MCI playback failure, etc.)
        # actually ends up somewhere checkable. Rotates at ~1MB so it
        # can't grow forever across a long-running stream. Import here
        # (not at module level) so paths.app_dir()'s frozen-exe check
        # runs after PyInstaller's own startup, not during it.
        from chatbot.core.paths import app_dir
        log_path = os.path.join(app_dir(), "lcbot.log")
        handlers.append(
            logging.handlers.RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        )
    except OSError:
        pass  # e.g. running from a read-only location -- console/StreamHandler still works

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    try:
        from chatbot.gui.main_window import run
    except ImportError as exc:
        print(f"Failed to import the app: {exc}")
        print("Make sure you're running this from the project folder with Python 3.10+.")
        sys.exit(1)
    run()


if __name__ == "__main__":
    main()
