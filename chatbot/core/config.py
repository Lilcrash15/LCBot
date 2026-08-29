"""Connection/secrets config, stored as JSON next to the app.

Kept separate from the SQLite database (core/database.py) on purpose:
this file holds credentials and connection info; the database holds
everything the GUI edits day-to-day (commands, currency settings,
moderation rules, timers, quotes, song queue).
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Dict


DEFAULT_CONFIG_PATH = os.path.join(os.getcwd(), "config.json")


@dataclass
class BotConfig:
    channel: str = ""                 # twitch channel to join, lowercase, no '#'
    bot_username: str = ""            # the account the bot chats as
    oauth_token: str = ""             # "oauth:xxxxxxxx" chat token for bot_username
    client_id: str = ""               # Twitch Dev Console app client id
    client_secret: str = ""           # Twitch Dev Console app client secret
    helix_access_token: str = ""      # user access token for Helix API calls
    helix_refresh_token: str = ""
    youtube_api_key: str = ""         # Google Cloud API key with YouTube Data API v3 enabled
    broadcaster_username: str = ""    # usually same as channel; kept separate for host/team accounts
    discord_webhook_url: str = ""     # Discord channel webhook for went-live announcements
    discord_announce_enabled: bool = False
    discord_went_live_message: str = (
        "\U0001F534 {channel} just went live on Twitch! {title} -- playing {game} "
        "-- https://twitch.tv/{channel}"
    )
    autoconnect: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def is_ready_to_connect(self) -> bool:
        return bool(self.channel and self.bot_username and self.oauth_token)


class ConfigStore:
    """Thread-safe load/save wrapper around a BotConfig backed by JSON."""

    def __init__(self, path: str = DEFAULT_CONFIG_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.data = BotConfig()
        self.load()

    def load(self) -> BotConfig:
        with self._lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as fh:
                        raw = json.load(fh)
                    known = {k: v for k, v in raw.items() if k in BotConfig.__dataclass_fields__}
                    extra = {k: v for k, v in raw.items() if k not in BotConfig.__dataclass_fields__}
                    self.data = BotConfig(**known)
                    self.data.extra.update(extra)
                except (json.JSONDecodeError, TypeError, ValueError):
                    # Corrupt config: keep defaults rather than crash the app.
                    self.data = BotConfig()
            return self.data

    def save(self) -> None:
        with self._lock:
            tmp_path = self.path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(asdict(self.data), fh, indent=2, sort_keys=True)
            os.replace(tmp_path, self.path)

    def update(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if key in BotConfig.__dataclass_fields__:
                setattr(self.data, key, value)
            else:
                self.data.extra[key] = value
        self.save()
