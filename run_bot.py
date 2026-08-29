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
import sys


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
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
