"""Scheduled chat announcements ("!discord every 15 minutes", etc.).

Each timer only fires once its interval has elapsed AND chat has seen
at least `min_messages_since_last` messages since the timer last
fired -- mirrors AnkhBot's "don't spam an empty/dead chat" behavior.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger("chatbot.timers")


class TimersModule:
    def __init__(self, db):
        self.db = db
        self._messages_since: dict[int, int] = {}

    def on_chat_message(self) -> None:
        for row in self.db.all_timers():
            self._messages_since[row["id"]] = self._messages_since.get(row["id"], 0) + 1

    def tick(self) -> list[str]:
        """Call periodically (e.g. every 30-60s). Returns messages ready to send."""
        if not self.db.get_setting_bool("timers_global_enabled", True):
            return []
        now = time.time()
        due: list[str] = []
        for row in self.db.all_timers():
            if not row["enabled"]:
                continue
            elapsed_minutes = (now - (row["last_fired"] or 0)) / 60.0
            if elapsed_minutes < row["interval_minutes"]:
                continue
            seen = self._messages_since.get(row["id"], 0)
            if seen < row["min_messages_since_last"]:
                continue
            due.append(row["message"])
            self.db.mark_timer_fired(row["id"])
            self._messages_since[row["id"]] = 0
        return due
