"""Downloads a YouTube video's audio-only stream to a local file so
chatbot.core.audio_player can play it directly inside this process --
that's what lets OBS's "Application Audio Capture" pick up song-request
audio from the bot itself, instead of needing a Browser Source pointed
at overlay/song_overlay.html. Ryan asked for this specifically.

Two things worth knowing before touching this file:

1. This is LCBot's one and only third-party dependency -- yt-dlp
   (github.com/yt-dlp/yt-dlp). Everything else in the app, including
   the original song-request overlay this sits alongside, is pure
   standard library (see requirements.txt). yt-dlp fetches the actual
   audio stream directly from YouTube's CDN, bypassing YouTube's own
   player entirely -- which is genuinely outside what YouTube's Terms
   of Service allow (their ToS only permits playback "through the
   video playback pages of the Service itself"). This is the same
   territory a huge number of very widely used tools already operate
   in (yt-dlp itself has millions of users) with essentially no
   enforcement against individual end users in practice, but it's a
   real, deliberate difference from the overlay approach, which stays
   inside YouTube's official IFrame Player and therefore inside their
   ToS. Ryan asked for local playback aware of that tradeoff -- see the
   project doc for the actual conversation. Because of this, local
   playback defaults to OFF (see database.py's
   "songrequests_local_playback_enabled") even though Ryan wants it on
   for himself -- every other streamer who downloads LCBot gets the
   ToS-clean browser overlay by default and opts into this deliberately.
2. yt-dlp needs to keep up with YouTube's frequent internal changes to
   keep working at all -- unlike the rest of this app, this is the one
   piece that can start failing with no code change here, whenever
   YouTube tweaks something upstream. If song downloads suddenly break,
   check yt-dlp's own release notes / GitHub issues first; a `pip
   install -U yt-dlp` (and matching PyInstaller rebuild) is usually the
   fix.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("chatbot.youtube_audio")

# How many downloaded song files to keep around at once. Each is
# typically a few MB (audio-only, a handful of minutes); this just
# keeps a long stream from slowly filling the disk, mirroring
# emote_cache.py's "cache it locally, don't let it grow forever" idea.
CACHE_MAX_FILES = 20


class YouTubeAudioError(Exception):
    """Raised for anything that stops a song's audio from being ready
    to play -- missing dependency, network failure, extraction failure,
    a private/region-locked/removed video, disk trouble. Always safe to
    show the message directly in chat or the GUI."""


def _import_yt_dlp():
    try:
        import yt_dlp  # local import: keeps app startup fast and turns
                        # a missing/not-yet-installed yt-dlp into a
                        # normal catchable error instead of crashing
                        # the whole app at launch over one optional
                        # feature.
    except ImportError as exc:
        raise YouTubeAudioError(
            "yt-dlp isn't installed -- local song playback needs it "
            "(pip install yt-dlp, or rebuild the exe with it in requirements.txt)."
        ) from exc
    return yt_dlp


def download_audio(video_id: str, cache_dir: str) -> str:
    """Downloads video_id's best available audio-only stream (preferring
    .m4a -- see audio_player.py's docstring for why) into cache_dir,
    returning the local file path. Reuses an already-downloaded file
    for the same video_id instead of re-fetching it. Raises
    YouTubeAudioError, with a message safe to show a streamer directly,
    on any failure."""
    os.makedirs(cache_dir, exist_ok=True)
    existing = _find_cached(cache_dir, video_id)
    if existing:
        return existing

    # Only actually needs yt-dlp importable when a real download is
    # about to happen -- an already-cached song plays even if yt-dlp
    # somehow isn't installed (or got uninstalled) after it was fetched.
    yt_dlp = _import_yt_dlp()
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": os.path.join(cache_dir, f"{video_id}.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # Only the YouTube extractor is ever needed here (video_id
        # always comes from youtube_api.py) -- keeps yt-dlp from
        # probing other site extractors for a URL that's always the
        # same shape.
        "allowed_extractors": ["youtube"],
        "socket_timeout": 20,
    }
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
    except Exception as exc:  # yt-dlp's own DownloadError plus anything else it can raise
        raise YouTubeAudioError(f"couldn't download audio for that video ({exc})") from exc

    if not os.path.exists(path):
        raise YouTubeAudioError("download finished but the audio file is missing")

    _prune_cache(cache_dir, keep=CACHE_MAX_FILES)
    return path


def _find_cached(cache_dir: str, video_id: str) -> Optional[str]:
    if not os.path.isdir(cache_dir):
        return None
    prefix = video_id + "."
    for name in os.listdir(cache_dir):
        if name.startswith(prefix):
            candidate = os.path.join(cache_dir, name)
            if os.path.isfile(candidate):
                return candidate
    return None


def _prune_cache(cache_dir: str, keep: int) -> None:
    """Deletes the oldest cached audio files once there are more than
    `keep` of them. Best-effort -- a failure here (e.g. a file still
    open elsewhere) should never take down song playback."""
    try:
        files = [os.path.join(cache_dir, f) for f in os.listdir(cache_dir)]
        files = [f for f in files if os.path.isfile(f)]
        if len(files) <= keep:
            return
        files.sort(key=os.path.getmtime)
        for f in files[: len(files) - keep]:
            try:
                os.remove(f)
            except OSError:
                pass
    except OSError:
        logger.exception("failed to prune song audio cache")
