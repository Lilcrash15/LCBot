"""Chat filters: links, excessive caps, symbol spam, banned phrases, and
message repetition -- with an escalating strikes -> timeout response.
Mods and the broadcaster are always exempt. This module only decides
*what* to do; the actual delete/timeout/ban is issued as an IRC
PRIVMSG chat command (/timeout, /delete, /ban), which is how Twitch
chat moderation has always worked over IRC.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from chatbot.core.irc_client import ChatMessage

logger = logging.getLogger("chatbot.moderation")

_URL_RE = re.compile(r"(?:https?://)?(?:www\.)?([a-zA-Z0-9-]+\.[a-zA-Z]{2,})(?:/\S*)?")
_LAST_MESSAGE_BY_USER: dict[str, str] = {}


@dataclass
class ModerationAction:
    delete: bool = False
    timeout_seconds: int = 0
    ban: bool = False
    reason: str = ""
    warn_message: str = ""


class ModerationModule:
    def __init__(self, db):
        self.db = db

    def _exempt(self, message: ChatMessage) -> bool:
        return message.is_mod or message.is_broadcaster

    def check_message(self, message: ChatMessage) -> Optional[ModerationAction]:
        if not self.db.get_setting_bool("moderation_enabled", True):
            return None
        if self._exempt(message):
            _LAST_MESSAGE_BY_USER[message.username] = message.text
            return None

        text = message.text.strip()
        reason = self._first_violation(message, text)
        if reason is None:
            _LAST_MESSAGE_BY_USER[message.username] = text
            return None

        strikes = self.db.add_strike(message.username)
        threshold = self.db.get_setting_int("moderation_strikes_before_timeout", 2)
        timeout_seconds = self.db.get_setting_int("moderation_timeout_seconds", 600)

        if strikes < threshold:
            return ModerationAction(
                delete=True,
                reason=reason,
                warn_message=f"@{message.display_name} watch it -- {reason}. ({strikes}/{threshold} strikes)",
            )
        self.db.reset_strikes(message.username)
        return ModerationAction(
            delete=True,
            timeout_seconds=timeout_seconds,
            reason=reason,
            warn_message=f"@{message.display_name} timed out for {timeout_seconds}s -- {reason}.",
        )

    def _first_violation(self, message: ChatMessage, text: str) -> Optional[str]:
        if self.db.get_setting_bool("moderation_banned_words_enabled", True):
            hit = self._banned_phrase_hit(text)
            if hit:
                return f"banned phrase ({hit})"

        if self.db.get_setting_bool("moderation_links_enabled", True):
            if self._contains_unwhitelisted_link(text):
                return "posting a link"

        if self.db.get_setting_bool("moderation_caps_enabled", True):
            min_len = self.db.get_setting_int("moderation_caps_min_len", 10)
            threshold = self.db.get_setting_int("moderation_caps_threshold_pct", 70)
            letters = [c for c in text if c.isalpha()]
            if len(text) >= min_len and letters:
                caps_pct = 100 * sum(1 for c in letters if c.isupper()) / len(letters)
                if caps_pct >= threshold:
                    return "excessive caps"

        if self.db.get_setting_bool("moderation_symbols_enabled", True):
            threshold = self.db.get_setting_int("moderation_symbols_threshold_pct", 50)
            if text:
                symbol_pct = 100 * sum(1 for c in text if not c.isalnum() and not c.isspace()) / len(text)
                if len(text) >= 6 and symbol_pct >= threshold:
                    return "symbol spam"

        if self.db.get_setting_bool("moderation_repetition_enabled", True):
            last = _LAST_MESSAGE_BY_USER.get(message.username)
            if last and last.strip().lower() == text.lower() and len(text) > 3:
                return "message repetition"

        return None

    def _banned_phrase_hit(self, text: str) -> Optional[str]:
        lowered = text.lower()
        for row in self.db.all_banned_phrases():
            phrase = row["phrase"]
            if row["is_regex"]:
                try:
                    if re.search(phrase, text, re.IGNORECASE):
                        return phrase
                except re.error:
                    continue
            elif phrase.lower() in lowered:
                return phrase
        return None

    def _contains_unwhitelisted_link(self, text: str) -> bool:
        whitelist = set(self.db.all_link_whitelist())
        for match in _URL_RE.finditer(text):
            domain = match.group(1).lower()
            if domain not in whitelist and not any(domain.endswith("." + w) for w in whitelist):
                return True
        return False
