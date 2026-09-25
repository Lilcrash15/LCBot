"""Fetches and caches Twitch emote and chat badge images for the
Console tab, so chat renders the way real Twitch chat looks instead of
raw text -- emotes inline in messages, badges (mod/vip/sub/etc.) next
to usernames. Pure standard library: Tk 8.6+'s PhotoImage decodes PNG
natively, so no Pillow/image library is needed.

Threading note: Tcl/Tk isn't thread-safe, so every PhotoImage is built
on the Tk main thread (wherever a caller calls get_emote_image /
get_badge_image from -- that should always be the GUI thread, e.g. from
_drain_queue). A first-time lookup for a given id does a small
synchronous HTTP fetch (blocks briefly); everything after that is
instant from the in-memory + on-disk cache.
"""
from __future__ import annotations

import base64
import logging
import os
import tkinter as tk
import urllib.error
import urllib.request
from typing import Callable, Optional

logger = logging.getLogger("chatbot.emote_cache")

EMOTE_URL_TMPL = "https://static-cdn.jtvnw.net/emoticons/v2/{id}/default/dark/1.0"


class EmoteBadgeCache:
    def __init__(
        self,
        cache_dir: str,
        get_twitch_api: Callable[[], Optional[object]],
        get_broadcaster_login: Callable[[], str],
    ):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._get_twitch_api = get_twitch_api
        self._get_broadcaster_login = get_broadcaster_login
        self._emote_images: dict[str, Optional[tk.PhotoImage]] = {}
        self._badge_images: dict[str, Optional[tk.PhotoImage]] = {}  # key "set_id/version"
        self._badge_urls: dict[str, str] = {}
        self._badges_loaded = False
        self._logged_no_api_yet = False

    # -- emotes ----------------------------------------------------------
    def get_emote_image(self, emote_id: str) -> Optional[tk.PhotoImage]:
        if emote_id in self._emote_images:
            return self._emote_images[emote_id]
        path = os.path.join(self.cache_dir, f"emote_{emote_id}.png")
        data = self._read_or_download(path, EMOTE_URL_TMPL.format(id=emote_id))
        image = self._to_photoimage(data)
        self._emote_images[emote_id] = image
        return image

    # -- badges ------------------------------------------------------------
    def get_badge_image(self, set_id: str, version: str) -> Optional[tk.PhotoImage]:
        self._ensure_badges_loaded()
        key = f"{set_id}/{version}"
        if key in self._badge_images:
            return self._badge_images[key]
        url = self._badge_urls.get(key)
        if not url:
            if self._badges_loaded:
                # Only cache "no such badge" once a real load has
                # actually completed -- otherwise a badge lookup that
                # comes in before/during a failed or still-pending
                # load (see _ensure_badges_loaded) would get written
                # off as permanently missing even though it may exist
                # once badges actually finish loading.
                self._badge_images[key] = None
            return None
        safe_key = key.replace("/", "_")
        path = os.path.join(self.cache_dir, f"badge_{safe_key}.png")
        data = self._read_or_download(path, url)
        image = self._to_photoimage(data)
        self._badge_images[key] = image
        return image

    def _ensure_badges_loaded(self) -> None:
        if self._badges_loaded:
            return
        api = self._get_twitch_api()
        if api is None:
            # Badges need the *broadcaster* to be authorized (self.bot.
            # twitch_api) -- being connected to chat only needs the bot
            # account, which is a very normal setup to stop at (nothing
            # else requires the broadcaster login). Logged once, not
            # per-message, so a "why are badges missing" report has a
            # concrete answer in lcbot.log instead of silence -- same
            # diagnostic-first approach as the taskbar icon investigation.
            if not self._logged_no_api_yet:
                self._logged_no_api_yet = True
                logger.info(
                    "chat badges unavailable: broadcaster isn't authorized yet "
                    "(Settings -> \"Log in with Twitch (streamer account)\")"
                )
            return  # not connected/authorized yet -- retry next time a badge is requested
        try:
            self._index_badge_set(api.get_global_badges())
            login = (self._get_broadcaster_login() or "").strip().lower()
            if login:
                broadcaster_id = api.get_user_id(login)
                if broadcaster_id:
                    self._index_badge_set(api.get_channel_badges(broadcaster_id))
        except Exception:
            # Found investigating a report of a specific badge (WizeBot's)
            # showing up missing in the Console tab (2026-09-07): this
            # used to set _badges_loaded = True *before* the fetch, so a
            # single transient failure here (a network hiccup right as
            # the app connects, before Twitch's API is fully reachable)
            # permanently marked every badge as "loaded" with an empty
            # _badge_urls table -- meaning literally every badge for
            # every user, for the rest of that run, would silently come
            # back as None and never even retry. Not setting the flag on
            # failure means the very next badge lookup (the next chat
            # message) tries again instead of staying broken forever.
            logger.exception("failed to load chat badges -- will retry on the next badge lookup")
            return
        self._badges_loaded = True

    def reset(self) -> None:
        """Called on every successful (re)connect (see MainWindow's
        "bot_connected" handling) so a fresh session always re-fetches
        badges instead of trusting a stale load from before a reconnect
        or a re-authorization (e.g. after picking up new scopes) --
        also gives a permanently-failed load (see _ensure_badges_loaded)
        a natural retry point even if no message happens to trigger one
        first."""
        self._badges_loaded = False
        self._badge_urls.clear()
        self._badge_images.clear()
        self._logged_no_api_yet = False

    def _index_badge_set(self, data: dict) -> None:
        for entry in data.get("data", []):
            set_id = entry.get("set_id", "")
            for version in entry.get("versions", []):
                key = f"{set_id}/{version.get('id')}"
                url = version.get("image_url_1x", "")
                if url:
                    self._badge_urls[key] = url

    # -- shared helpers ------------------------------------------------
    def _read_or_download(self, path: str, url: str) -> Optional[bytes]:
        if os.path.exists(path):
            try:
                with open(path, "rb") as fh:
                    return fh.read()
            except OSError:
                pass
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TwitchChatBot/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
        except (urllib.error.URLError, OSError):
            return None
        try:
            with open(path, "wb") as fh:
                fh.write(data)
        except OSError:
            pass
        return data

    def _to_photoimage(self, data: Optional[bytes]) -> Optional[tk.PhotoImage]:
        if not data:
            return None
        try:
            return tk.PhotoImage(data=base64.b64encode(data))
        except tk.TclError:
            logger.debug("PhotoImage decode failed (unsupported format?)")
            return None
