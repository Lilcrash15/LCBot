"""Turns the raw errors Twitch/Discord/network calls raise elsewhere in
the app into short, plain-English messages that say what to actually
do about it, instead of an HTTP status code or a raw exception string.

Kept separate from chatbot/gui/main_window.py (which is where all of
these get displayed) so it has no tkinter dependency and can be unit
tested directly -- see FriendlyErrorTests in tests/test_smoke.py.

TwitchAPIError (chatbot/modules/twitch_api.py) and the Discord
webhook's RuntimeError (chatbot/modules/discord_notify.py) both embed
the real HTTP status code and response body in their message text
rather than as a separate attribute, so this matches on that text
instead of the exception type -- it works the same whether it's
handed a live exception object or a string that already went through
str(exc) once (e.g. after crossing a background-thread queue, which
is how every caller in main_window.py actually uses this)."""
from __future__ import annotations

import re
from typing import Optional

_HTTP_CODE_RE = re.compile(r": (\d{3})(?:\s|$)")


def _extract_http_code(text: str) -> Optional[int]:
    m = _HTTP_CODE_RE.search(text)
    return int(m.group(1)) if m else None


def friendly_error_text(err: object) -> str:
    text = str(err)
    low = text.lower()
    code = _extract_http_code(text)

    if "discord webhook rejected" in low:
        if code in (401, 404):
            return (
                "Discord rejected that webhook -- double-check the Webhook URL in "
                "Settings under Discord Announcements (it may have been deleted or "
                "regenerated on Discord's side)."
            )
        return f"Discord rejected that message (error {code or '?'}). Try again in a moment."
    if "couldn't reach discord" in low or "could not reach discord" in low:
        return "Couldn't reach Discord -- check your internet connection and try again."

    if "helix" in low and "unreachable" in low:
        return "Couldn't reach Twitch -- check your internet connection and try again."
    if "helix" in low and "failed" in low:
        if code in (401, 403):
            return (
                "Twitch rejected that -- your Broadcaster access token has probably "
                "expired or is missing a permission. Click \"Authorize (broadcaster)\" "
                "again in Settings."
            )
        if code == 404:
            return "Twitch couldn't find that. Double-check your Twitch channel in Settings."
        return f"Twitch rejected that (error {code or '?'}). Try again in a moment."

    if isinstance(err, OSError):
        return "Couldn't connect -- check your internet connection and try again."

    # Anything else -- including Bot's own hand-written RuntimeErrors
    # like "Set 'Twitch channel to join' in Settings first." -- is
    # already plain English, so pass it through as-is.
    return text
