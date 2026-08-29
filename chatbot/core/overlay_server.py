"""Tiny local HTTP file server for the song-request overlay.

Why this exists: OBS/Streamlabs Desktop Browser Sources have a "Local
file" checkbox that loads the page over file://. That works fine for
static markup, but the overlay's JS polls a neighboring JSON file with
fetch() to find out what's currently playing -- and Chromium's CORS
rules block a file:// page from fetch()-ing anything, even a file
sitting right next to it ("Origin null" is not allowed to fetch). The
overlay would sit there forever showing "Waiting for a song request..."
no matter what the bot writes.

Serving the overlay folder over plain http://localhost instead sidesteps
this entirely: the page and the state file become a normal same-origin
pair, so fetch() just works. Point the Browser Source at the URL this
prints (Local file unchecked) instead of browsing to the .html file.
"""
from __future__ import annotations

import http.server
import logging
import os
import threading
from functools import partial
from typing import Optional

logger = logging.getLogger("chatbot.overlay_server")

DEFAULT_PORT = 17564


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # silence default stderr logging
        logger.debug("overlay server: " + format, *args)

    def end_headers(self) -> None:
        # The state file changes every few seconds; make sure OBS's
        # browser (and any other client) never serves a cached copy.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def start(directory: str, port: int = DEFAULT_PORT) -> Optional[http.server.ThreadingHTTPServer]:
    """Serve `directory` at http://localhost:<port>/ from a daemon thread.

    Returns the running server (call .shutdown() to stop it) or None if
    the port was already taken -- most likely another copy of the bot is
    already running and already serving it, which is harmless.
    """
    os.makedirs(directory, exist_ok=True)
    handler = partial(_QuietHandler, directory=directory)
    try:
        server = http.server.ThreadingHTTPServer(("localhost", port), handler)
    except OSError:
        logger.warning("overlay server: port %d already in use -- is the bot already running?", port)
        return None

    thread = threading.Thread(target=server.serve_forever, daemon=True, name="overlay-server")
    thread.start()
    logger.info("overlay server: serving %s at http://localhost:%d/", directory, port)
    return server
