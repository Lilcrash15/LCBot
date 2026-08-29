"""Discord "went live" webhook announcements.

No third-party dependencies -- a Discord webhook is just a plain HTTP
POST of {"content": "..."} to a URL Discord hands you when you add one
to a channel (Server Settings -> Integrations -> Webhooks -> New
Webhook -> Copy Webhook URL). This polls the same Twitch stream-info
endpoint the !uptime/!title/!game commands already use
(TwitchAPI.get_stream_info, which itself caches for 15s) on the bot's
existing 10s scheduler tick, rather than opening any connection of its
own -- it just checks "is the interval up yet" on every tick.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from chatbot.modules.twitch_api import TwitchAPI, TwitchAPIError

logger = logging.getLogger("chatbot.discord")

# How often to actually ask Twitch whether the stream is live. Doesn't
# need to be as tight as the 10s scheduler tick -- a minute's delay on
# a "went live" announcement is unnoticeable, and it keeps this from
# adding an API call to every single tick forever.
CHECK_INTERVAL_SECONDS = 60


class DiscordNotifier:
    def __init__(self, sender: Optional[Callable[[str, str], None]] = None):
        # None = "haven't established a baseline yet". The first check
        # after (re)connecting just records whatever the current live
        # state is without announcing -- otherwise, reopening the bot
        # while a stream that was already live would fire a false
        # "just went live" the moment it reconnects. Only an actual
        # offline -> live transition after that baseline announces.
        self._was_live: Optional[bool] = None
        self._last_check_at: float = 0.0
        # Tests inject a fake sender instead of hitting the real
        # network; production uses the real HTTP POST.
        self._sender = sender or self._http_post

    def reset(self) -> None:
        """Called from Bot.connect() so each connection re-establishes
        its own live/offline baseline instead of carrying state across
        a reconnect or a fresh app launch."""
        self._was_live = None
        self._last_check_at = 0.0

    def tick(
        self,
        twitch_api: Optional[TwitchAPI],
        channel: str,
        webhook_url: str,
        enabled: bool,
        message_template: str,
        now: Optional[float] = None,
    ) -> None:
        if not enabled or not webhook_url or not channel or twitch_api is None:
            return
        now = time.time() if now is None else now
        if now - self._last_check_at < CHECK_INTERVAL_SECONDS:
            return
        self._last_check_at = now
        try:
            info = twitch_api.get_stream_info(channel)
        except TwitchAPIError as exc:
            logger.warning("Discord went-live check couldn't reach Twitch: %s", exc)
            return

        if self._was_live is None:
            self._was_live = info.live
            return
        if info.live and not self._was_live:
            text = self._render(message_template, channel, info)
            try:
                self.send(webhook_url, text)
            except Exception:
                logger.exception("Discord went-live announcement failed to send")
        self._was_live = info.live

    @staticmethod
    def _render(message_template: str, channel: str, info) -> str:
        try:
            return message_template.format(channel=channel, title=info.title, game=info.game_name)
        except (KeyError, IndexError):
            # A typo'd {placeholder} in the template -- send it as-is
            # rather than silently eating the whole announcement.
            return message_template

    def send(self, webhook_url: str, text: str) -> None:
        """POSTs a plain message to a Discord webhook. Used for both
        the real went-live announcement and the Settings tab's "Send
        test message" button. Raises on failure so the test button can
        surface a real error instead of pretending it worked."""
        self._sender(webhook_url, text)

    @staticmethod
    def _http_post(webhook_url: str, text: str) -> None:
        payload = json.dumps({"content": text}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url, data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Discord webhook rejected the message: {exc.code} {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Couldn't reach Discord: {exc}") from exc
