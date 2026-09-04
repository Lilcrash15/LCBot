"""Follow / subscription / raid chat alerts.

Sub, resub, gift-sub, and raid events arrive in real time over Twitch
IRC as USERNOTICE messages (see core/irc_client.py's on_usernotice
callback) -- Twitch broadcasts these to everyone connected to chat, no
extra API scope needed, same as how the original AnkhBot picked them
up. New followers don't have a live chat event any more (Twitch
removed that years ago), so those are detected by polling the Get
Channel Followers Helix endpoint on the bot's existing scheduler tick
-- the same "is the interval up yet" pattern discord_notify.py uses
for went-live checks -- and diffing against the follower ids already
seen.

All four alert types (and the bot as a whole) can be turned off from
Settings, and every message is an editable template stored in the
settings table (see database.py's DEFAULT_SETTINGS), same as the
Discord went-live message.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from chatbot.core.database import Database
from chatbot.modules.twitch_api import TwitchAPI, TwitchAPIError

logger = logging.getLogger("chatbot.alerts")

# Doesn't need to be as tight as the 10s scheduler tick -- a minute's
# delay noticing a new follower is unnoticeable, and it keeps this
# from adding a Helix call to every single tick forever.
FOLLOWER_CHECK_INTERVAL_SECONDS = 60


class AlertsModule:
    def __init__(self, db: Database):
        self.db = db
        self._last_follower_check: float = 0.0
        # None = "haven't established a baseline yet". The first
        # follower check after (re)connecting just records whoever's
        # currently following without announcing them -- otherwise
        # reconnecting (or the very first connect ever) would "thank"
        # every one of your existing followers all at once, exactly
        # the false-announce bug DiscordNotifier.reset() avoids for
        # went-live.
        self._known_follower_ids: Optional[set] = None

    def reset_session(self) -> None:
        """Called from Bot.connect() so each connection re-establishes
        its own follower baseline instead of carrying state across a
        reconnect or app relaunch."""
        self._last_follower_check = 0.0
        self._known_follower_ids = None

    # -- USERNOTICE: sub / resub / subgift / raid (event-driven) --------
    def handle_usernotice(self, tags: dict) -> Optional[str]:
        """tags is the parsed IRC tag dict for a USERNOTICE line (see
        irc_client.py's _parse_tags). Returns a chat message to send,
        or None if this event type is disabled or not one we announce."""
        if not self.db.get_setting_bool("alerts_enabled", True):
            return None
        msg_id = tags.get("msg-id", "")
        display_name = tags.get("display-name") or tags.get("login") or "someone"

        if msg_id in ("sub", "resub"):
            if not self.db.get_setting_bool("alerts_sub_enabled", True):
                return None
            if msg_id == "resub":
                months = tags.get("msg-param-cumulative-months", "1")
                return self._render("alerts_resub_message", user=display_name, months=months)
            return self._render("alerts_sub_message", user=display_name)

        if msg_id in ("subgift", "anonsubgift"):
            if not self.db.get_setting_bool("alerts_sub_enabled", True):
                return None
            recipient = tags.get("msg-param-recipient-display-name", "someone")
            return self._render("alerts_subgift_message", user=display_name, recipient=recipient)

        if msg_id == "raid":
            if not self.db.get_setting_bool("alerts_raid_enabled", True):
                return None
            viewers = tags.get("msg-param-viewerCount", "0")
            return self._render("alerts_raid_message", user=display_name, viewers=viewers)

        return None

    # -- new-follower polling --------------------------------------------
    def tick(self, twitch_api: Optional[TwitchAPI], channel: str, now: Optional[float] = None) -> list:
        """Returns zero or more chat messages for newly-seen followers.
        Safe to call on every scheduler tick -- internally throttled to
        FOLLOWER_CHECK_INTERVAL_SECONDS, same pattern as
        DiscordNotifier.tick."""
        if not self.db.get_setting_bool("alerts_enabled", True):
            return []
        if not self.db.get_setting_bool("alerts_follow_enabled", True):
            return []
        if twitch_api is None or not channel:
            return []

        now = time.time() if now is None else now
        if now - self._last_follower_check < FOLLOWER_CHECK_INTERVAL_SECONDS:
            return []
        self._last_follower_check = now

        try:
            followers = twitch_api.get_recent_followers(channel, first=10)
        except TwitchAPIError as exc:
            logger.warning("Follower alert check couldn't reach Twitch: %s", exc)
            return []

        ids = [f["user_id"] for f in followers]
        if self._known_follower_ids is None:
            self._known_follower_ids = set(ids)
            return []

        new_ones = [f for f in followers if f["user_id"] not in self._known_follower_ids]
        self._known_follower_ids.update(ids)
        if not new_ones:
            return []
        # Twitch returns most-recent-first; reverse so a burst of
        # follows announces in the order they actually happened.
        return [
            self._render("alerts_follow_message", user=f.get("user_name") or f.get("user_login", "someone"))
            for f in reversed(new_ones)
        ]

    def _render(self, setting_key: str, **kwargs) -> str:
        template = self.db.get_setting(setting_key, "") or ""
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            # A typo'd {placeholder} in a custom template -- send it
            # as-is rather than silently eating the whole alert.
            return template
