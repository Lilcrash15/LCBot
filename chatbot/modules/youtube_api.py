"""Small YouTube Data API v3 wrapper: resolve a search query or URL to a
video id/title/duration for the song request queue. Needs a Google
Cloud API key with the YouTube Data API v3 enabled (see README)."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

YT_BASE = "https://www.googleapis.com/youtube/v3"

_URL_ID_PATTERNS = [
    re.compile(r"(?:youtube\.com/watch\?v=|youtube\.com/live/|youtu\.be/|youtube\.com/shorts/)([A-Za-z0-9_-]{11})"),
]
_ISO8601_DURATION_RE = re.compile(
    r"P(?:\d+D)?T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?"
)


class YouTubeAPIError(Exception):
    pass


@dataclass
class YouTubeVideo:
    video_id: str
    title: str
    duration_seconds: int


def extract_video_id(text: str) -> Optional[str]:
    for pattern in _URL_ID_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def parse_iso8601_duration(value: str) -> int:
    m = _ISO8601_DURATION_RE.match(value or "")
    if not m:
        return 0
    hours = int(m.group("h") or 0)
    minutes = int(m.group("m") or 0)
    seconds = int(m.group("s") or 0)
    return hours * 3600 + minutes * 60 + seconds


class YouTubeAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def _get(self, path: str, params: dict) -> dict:
        params = {**params, "key": self.api_key}
        url = f"{YT_BASE}{path}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise YouTubeAPIError(f"YouTube API error {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise YouTubeAPIError(f"YouTube API unreachable: {exc}") from exc

    def get_video(self, video_id: str) -> Optional[YouTubeVideo]:
        data = self._get("/videos", {"id": video_id, "part": "snippet,contentDetails"})
        items = data.get("items", [])
        if not items:
            return None
        item = items[0]
        duration = parse_iso8601_duration(item["contentDetails"]["duration"])
        return YouTubeVideo(video_id=video_id, title=item["snippet"]["title"], duration_seconds=duration)

    def search_first(self, query: str) -> Optional[YouTubeVideo]:
        data = self._get("/search", {"q": query, "part": "snippet", "type": "video", "maxResults": 1})
        items = data.get("items", [])
        if not items:
            return None
        video_id = items[0]["id"]["videoId"]
        return self.get_video(video_id)

    def resolve(self, text: str) -> Optional[YouTubeVideo]:
        video_id = extract_video_id(text)
        if video_id:
            return self.get_video(video_id)
        return self.search_first(text)
