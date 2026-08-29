"""Event System: fire a message (and optionally an SFX) when a viewer
joins the channel, or when they speak in chat for the first time this
session. Two independent lists -- "On Join" and "On Speak" -- each row
targets either everyone or one specific user. Matches AnkhBot's
behavior of re-arming every restart: the seen-this-session sets are
in-memory only, so events fire again next time the bot connects.
"""
from __future__ import annotations

import logging
from typing import Optional

from chatbot.core.irc_client import ChatMessage
from chatbot.modules.commands import CommandContext, default_variable_resolver

logger = logging.getLogger("chatbot.event_system")


class EventSystemModule:
    def __init__(self, db, sfx_module=None):
        self.db = db
        self.sfx = sfx_module
        self._seen_join: set[str] = set()
        self._seen_speak: set[str] = set()

    def reset_session(self) -> None:
        """Call on each new connect so events re-fire, like AnkhBot does."""
        self._seen_join.clear()
        self._seen_speak.clear()

    def on_user_join(self, username: str, bot: "object") -> Optional[str]:
        if username in self._seen_join:
            return None
        self._seen_join.add(username)
        return self._fire("join", username, bot)

    def on_user_speak(self, message: ChatMessage, bot: "object") -> Optional[str]:
        if message.username in self._seen_speak:
            return None
        self._seen_speak.add(message.username)
        return self._fire("speak", message.username, bot, message=message)

    def _fire(self, trigger_type: str, username: str, bot: "object",
              message: Optional[ChatMessage] = None) -> Optional[str]:
        rows = [r for r in self.db.all_events(trigger_type) if r["enabled"]]
        matched = None
        for row in rows:
            if row["user_group"] == "user_specific":
                if row["username"] and row["username"].lower() == username.lower():
                    matched = row
                    break
            elif row["user_group"] == "everyone":
                matched = matched or row
        if matched is None:
            return None

        if matched["sfx_name"] and self.sfx is not None:
            sfx_row = self.db.get_sfx(matched["sfx_name"])
            if sfx_row:
                self.sfx.play(sfx_row["file_path"], sfx_row["volume"])

        if not matched["message"]:
            return None

        synthetic = message or ChatMessage(
            raw="", username=username, display_name=username.capitalize(),
            channel=getattr(bot, "streaminfo", None) and bot.streaminfo.channel or "",
            text="",
        )
        ctx = CommandContext(message=synthetic, args=[], bot=bot)
        return default_variable_resolver(matched["message"], ctx)
