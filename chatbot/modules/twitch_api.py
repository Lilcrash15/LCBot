"""Thin wrapper around Twitch's Helix REST API.

Used for the stuff Twitch IRC doesn't tell you: follower counts,
follow dates, stream uptime/title/game. Requires a Client-ID (from a
registered Twitch Dev Console app) and a user access token with at
least no special scopes for public read endpoints -- moderation
actions elsewhere in the app use IRC commands (/timeout, /ban) instead
of the API, so no extra scopes are required just to run the bot.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("chatbot.twitch_api")

HELIX_BASE = "https://api.twitch.tv/helix"


class TwitchAPIError(Exception):
    pass


@dataclass
class StreamInfo:
    live: bool
    title: str = ""
    game_name: str = ""
    started_at: Optional[float] = None  # unix timestamp
    viewer_count: int = 0


class TwitchAPI:
    def __init__(self, client_id: str, access_token: str):
        self.client_id = client_id
        self.access_token = access_token
        self._user_id_cache: dict[str, str] = {}
        self._cache: dict[str, tuple[float, object]] = {}

    def _headers(self) -> dict:
        token = self.access_token
        if token.startswith("oauth:"):
            token = token[len("oauth:"):]
        return {
            "Client-Id": self.client_id,
            "Authorization": f"Bearer {token}",
        }

    def _get(self, path: str, params: dict, cache_seconds: float = 0.0) -> dict:
        cache_key = path + "?" + urllib.parse.urlencode(sorted(params.items()))
        if cache_seconds:
            cached = self._cache.get(cache_key)
            if cached and time.time() - cached[0] < cache_seconds:
                return cached[1]  # type: ignore[return-value]

        url = f"{HELIX_BASE}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise TwitchAPIError(f"Helix {path} failed: {exc.code} {body}") from exc
        except urllib.error.URLError as exc:
            raise TwitchAPIError(f"Helix {path} unreachable: {exc}") from exc

        if cache_seconds:
            self._cache[cache_key] = (time.time(), data)
        return data

    def get_user_id(self, login: str) -> Optional[str]:
        login = login.lower()
        if login in self._user_id_cache:
            return self._user_id_cache[login]
        data = self._get("/users", {"login": login}, cache_seconds=3600)
        users = data.get("data", [])
        if not users:
            return None
        user_id = users[0]["id"]
        self._user_id_cache[login] = user_id
        return user_id

    def get_stream_info(self, channel_login: str) -> StreamInfo:
        data = self._get("/streams", {"user_login": channel_login.lower()}, cache_seconds=15)
        streams = data.get("data", [])
        if not streams:
            return StreamInfo(live=False)
        s = streams[0]
        started = None
        try:
            started = time.mktime(time.strptime(s["started_at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
        except (KeyError, ValueError):
            pass
        return StreamInfo(
            live=True,
            title=s.get("title", ""),
            game_name=s.get("game_name", ""),
            started_at=started,
            viewer_count=s.get("viewer_count", 0),
        )

    def get_channel_info(self, broadcaster_login: str) -> dict:
        """Current title/game for the channel via the Channels endpoint
        rather than /streams -- unlike get_stream_info, this works even
        while offline, which is what the pencil-icon "update title/game"
        dialog needs to pre-fill with the current values regardless of
        live status. Returns {} if the broadcaster login can't be
        resolved (e.g. nothing typed into Settings yet)."""
        broadcaster_id = self.get_user_id(broadcaster_login)
        if not broadcaster_id:
            return {}
        data = self._get("/channels", {"broadcaster_id": broadcaster_id})
        rows = data.get("data", [])
        return rows[0] if rows else {}

    def search_categories(self, query: str, limit: int = 20) -> list[dict]:
        """Twitch's own game/category search -- used by the "update
        title/game" dialog so Ryan picks from Twitch's actual list
        instead of typing a category name that might not match exactly
        (Twitch matches game_id, not free text, when setting a
        channel's category). Returns [{'id': ..., 'name': ...}, ...]."""
        if not query.strip():
            return []
        data = self._get("/search/categories", {"query": query, "first": limit})
        return [{"id": row["id"], "name": row["name"]} for row in data.get("data", [])]

    def modify_channel_info(
        self, broadcaster_id: str, title: Optional[str] = None, game_id: Optional[str] = None
    ) -> None:
        """Updates the channel's title and/or category via Twitch's
        Modify Channel Information endpoint. Requires the broadcaster's
        own token with the channel:manage:broadcast scope. Only fields
        that are not None are sent, so a title-only or game-only update
        doesn't touch the other."""
        payload: dict = {}
        if title is not None:
            payload["title"] = title
        if game_id is not None:
            payload["game_id"] = game_id
        if not payload:
            return
        url = f"{HELIX_BASE}/channels?broadcaster_id={urllib.parse.quote(broadcaster_id)}"
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="PATCH")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise TwitchAPIError(f"Helix modify channel info failed: {exc.code} {body}") from exc
        except urllib.error.URLError as exc:
            raise TwitchAPIError(f"Helix modify channel info unreachable: {exc}") from exc

    def get_follower_count(self, broadcaster_login: str) -> int:
        broadcaster_id = self.get_user_id(broadcaster_login)
        if not broadcaster_id:
            return 0
        data = self._get(
            "/channels/followers", {"broadcaster_id": broadcaster_id, "first": 1}, cache_seconds=30
        )
        return int(data.get("total", 0))

    def get_global_badges(self) -> dict:
        """Twitch's site-wide chat badges (broadcaster, moderator, VIP,
        Prime, bit tiers, etc.) -- keyed by set_id/version by the caller."""
        return self._get("/chat/badges/global", {}, cache_seconds=3600)

    def get_channel_badges(self, broadcaster_id: str) -> dict:
        """Channel-specific badges (subscriber months, custom sub badges)
        that only exist for this broadcaster."""
        return self._get("/chat/badges", {"broadcaster_id": broadcaster_id}, cache_seconds=3600)

    def send_chat_message(self, broadcaster_id: str, sender_id: str, message: str) -> None:
        """Posts a chat message via the Helix Chat API rather than IRC --
        used for "send as the streamer" from the GUI's identity dropdown,
        since that needs the broadcaster's own user token (scope
        user:write:chat) rather than the bot account's IRC connection."""
        url = f"{HELIX_BASE}/chat/messages"
        payload = json.dumps({
            "broadcaster_id": broadcaster_id,
            "sender_id": sender_id,
            "message": message,
        }).encode("utf-8")
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise TwitchAPIError(f"Helix send chat message failed: {exc.code} {body}") from exc
        except urllib.error.URLError as exc:
            raise TwitchAPIError(f"Helix send chat message unreachable: {exc}") from exc

    def get_recent_followers(self, broadcaster_login: str, first: int = 10) -> list[dict]:
        """Returns the most recently followed users, newest first, as
        [{'user_id', 'user_login', 'user_name', 'followed_at'}, ...].
        Used to detect new followers by polling (Twitch has no live
        "someone followed" chat/IRC event any more). Requires
        moderator:read:followers on the broadcaster's own token (or a
        moderator's). Deliberately not cached -- the alerts poll
        already throttles how often this is called (see
        modules/alerts.py), and caching here would delay noticing a
        follow by however long the cache window is on top of that."""
        broadcaster_id = self.get_user_id(broadcaster_login)
        if not broadcaster_id:
            return []
        data = self._get("/channels/followers", {"broadcaster_id": broadcaster_id, "first": first})
        return list(data.get("data", []))

    def get_follow_info(self, broadcaster_login: str, user_login: str) -> Optional[dict]:
        """Returns {'followed_at': iso8601 str} or None if not following.
        Requires moderator:read:followers scope on the token for the broadcaster's own channel."""
        broadcaster_id = self.get_user_id(broadcaster_login)
        user_id = self.get_user_id(user_login)
        if not broadcaster_id or not user_id:
            return None
        try:
            data = self._get(
                "/channels/followers",
                {"broadcaster_id": broadcaster_id, "user_id": user_id},
                cache_seconds=10,
            )
        except TwitchAPIError:
            return None
        rows = data.get("data", [])
        return rows[0] if rows else None
