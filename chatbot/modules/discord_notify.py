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

from chatbot import __version__
from chatbot.modules.twitch_api import TwitchAPI, TwitchAPIError

logger = logging.getLogger("chatbot.discord")

# How often to actually ask Twitch whether the stream is live. Doesn't
# need to be as tight as the 10s scheduler tick -- a minute's delay on
# a "went live" announcement is unnoticeable, and it keeps this from
# adding an API call to every single tick forever.
CHECK_INTERVAL_SECONDS = 60

# Persisted (chatbot.db, not in-memory) so "have I already announced
# THIS broadcast" survives an app restart, a reconnect, or opening the
# bot after the stream already started -- see the class docstring for
# why in-memory-only tracking couldn't do this.
LAST_ANNOUNCED_STREAM_SETTING_KEY = "discord_last_announced_stream_started_at"

# Twitch's own timestamps round-trip through str storage (they're
# whole seconds to begin with), but this gives a little slack rather
# than requiring exact float equality to recognize "same broadcast."
_SAME_STREAM_TOLERANCE_SECONDS = 1.0


class DiscordNotifier:
    """Posts a "went live" message to Discord once per real Twitch
    broadcast -- identified by Twitch's own per-stream started_at
    timestamp, persisted to the database rather than kept only in
    memory, specifically so it's correct regardless of *when* the bot
    happens to be running relative to the stream: connecting before it
    starts (catches the offline -> live transition directly), after it
    already started (Ryan, 2026-09-25: "a lot of programs I have to
    open when I stream" -- the bot isn't always first), or reconnecting
    mid-stream for any reason (auto-reconnect, a manual reconnect, a
    full app restart) -- none of those re-announce, since it's still
    the same started_at as whatever was already announced. Only a
    genuinely new broadcast (a new started_at) ever announces again."""

    def __init__(self, db=None, sender: Optional[Callable[[str, str], None]] = None):
        self.db = db
        # In-memory fallback only -- used when Twitch didn't give us a
        # parseable started_at (see tick()), which the persisted-key
        # path above doesn't need at all. None = "no baseline yet."
        self._was_live: Optional[bool] = None
        self._last_check_at: float = 0.0
        # Tests inject a fake sender instead of hitting the real
        # network; production uses the real HTTP POST.
        self._sender = sender or self._http_post
        # See tick()'s logging -- each logged once (not every 10s tick)
        # so a "why didn't it announce" report has real data instead of
        # silence, without flooding lcbot.log. Reset alongside the
        # baseline itself so a fresh connect gets fresh diagnostics too.
        self._logged_guard_skip = False
        self._logged_baseline = False
        self._logged_already_announced = False

    def reset(self) -> None:
        """Called from Bot.connect() so each connection re-establishes
        its own in-memory baseline/diagnostic-logging state -- does
        NOT touch the persisted last-announced-stream key in the
        database, which is deliberately meant to survive exactly the
        kind of reconnect/restart this clears everything else for."""
        self._was_live = None
        self._last_check_at = 0.0
        self._logged_guard_skip = False
        self._logged_baseline = False
        self._logged_already_announced = False

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
            # Logged once per connection (2026-09-22, after a report of
            # "I'm live now and nothing was sent" that this file had no
            # way to actually diagnose -- every branch below this one
            # was equally silent on success as on "nothing to do", so a
            # missing announcement looked identical to a real bug no
            # matter which of these it actually was). twitch_api is
            # None specifically means the *streamer* account isn't
            # authorized yet (Settings -> "Log in with Twitch (streamer
            # account)") -- being connected to chat only needs the bot
            # account, same distinction the chat-badge loading code
            # already logs separately.
            if not self._logged_guard_skip:
                self._logged_guard_skip = True
                reasons = []
                if not enabled:
                    reasons.append("\"Announce in Discord when I go live\" isn't checked in Settings")
                if not webhook_url:
                    reasons.append("no Discord webhook URL is set")
                if not channel:
                    reasons.append("no Twitch channel is set")
                if twitch_api is None:
                    reasons.append("the streamer account isn't authorized yet (Settings -> \"Log in with Twitch (streamer account)\")")
                logger.info("Discord went-live announcements are off: %s", "; ".join(reasons))
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

        if not info.live:
            self._was_live = False
            if not self._logged_baseline:
                self._logged_baseline = True
                logger.info("Discord went-live baseline: channel is offline -- will announce once it goes live.")
            return

        if info.started_at is None:
            # Extremely rare (a malformed started_at from Twitch --
            # see TwitchAPI.get_stream_info) -- without a real per-
            # broadcast key to dedup against, fall back to the
            # original offline -> live transition check so this at
            # least doesn't re-announce on every single tick.
            if self._was_live is None:
                self._was_live = True
                logger.warning(
                    "Discord went-live: channel is live but Twitch didn't give a usable start time -- "
                    "falling back to only announcing on the next offline -> live transition this session."
                )
                return
            if not self._was_live:
                self._announce(webhook_url, message_template, channel, info)
            self._was_live = True
            return

        last_announced = self.db.get_setting_float(LAST_ANNOUNCED_STREAM_SETTING_KEY, 0.0) if self.db else 0.0
        if last_announced and abs(info.started_at - last_announced) < _SAME_STREAM_TOLERANCE_SECONDS:
            # Already announced this exact broadcast -- could be a
            # normal 60s re-check mid-stream, a reconnect, or the app
            # itself having been restarted; either way, nothing to do.
            if not self._logged_already_announced:
                self._logged_already_announced = True
                logger.info("Discord went-live: this broadcast was already announced -- not re-announcing.")
            self._was_live = True
            return

        if self._was_live is None:
            logger.info(
                "Discord went-live: channel was already live when this connected, and hasn't been "
                "announced yet for this broadcast -- sending the announcement now."
            )
        else:
            logger.info("Discord went-live: channel just went live -- sending announcement.")
        self._announce(webhook_url, message_template, channel, info, stream_started_at=info.started_at)
        self._was_live = True

    def _announce(self, webhook_url, message_template, channel, info, stream_started_at=None) -> None:
        text = self.render(message_template, channel, info)
        try:
            self.send(webhook_url, text)
            logger.info("Discord went-live announcement sent.")
            self.record_announced_broadcast(stream_started_at)
        except Exception:
            # Deliberately doesn't record the last-announced key on
            # failure -- a failed send (webhook down, Discord hiccup)
            # should retry on the next eligible tick instead of being
            # silently marked "done" when it wasn't.
            logger.exception("Discord went-live announcement failed to send")

    def record_announced_broadcast(self, started_at: Optional[float]) -> None:
        """Marks the given broadcast (Twitch's own started_at) as
        already announced, so tick() doesn't send a duplicate shortly
        after. Public so the Settings tab's manual "Send now" backup
        button can call it too -- that button sends via self.send()
        directly, entirely outside tick()'s own dedup check, so
        without this a manual send followed by the automatic check
        picking up the same still-live broadcast would double-post."""
        if started_at is not None and self.db:
            self.db.set_setting(LAST_ANNOUNCED_STREAM_SETTING_KEY, started_at)

    @staticmethod
    def render(message_template: str, channel: str, info) -> str:
        """Fills {channel}/{title}/{game} into a went-live message
        template. Public (not just used internally by tick()) so the
        Settings tab's manual "Send now" backup button (added 2026-
        09-22) can build the exact same real message the automatic
        path would have sent, instead of duplicating this logic."""
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
            webhook_url,
            data=payload,
            # Discord sits behind Cloudflare, which blocks urllib's own
            # default User-Agent ("Python-urllib/3.x") as a generic
            # scripted client -- a real, well-documented issue (2026-
            # 09-22, confirmed by multiple independent projects hitting
            # the exact same thing, e.g. github.com/pradyb/herdr-notify-
            # router#9): Cloudflare error 1010, not Discord's own
            # webhook logic rejecting anything about the message itself.
            # Ryan hit this directly -- "Send test message" came back
            # "Discord webhook rejected the message: 403 ...". Any real
            # User-Agent string clears it; using the same pattern
            # emote_cache.py already uses for Twitch's CDN.
            headers={"Content-Type": "application/json", "User-Agent": f"LCBot/{__version__}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Discord webhook rejected the message: {exc.code} {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Couldn't reach Discord: {exc}") from exc
