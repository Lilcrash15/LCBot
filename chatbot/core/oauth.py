"""Twitch OAuth implicit-grant helper.

Twitch's implicit flow returns the access token in the URL *fragment*
(#access_token=...), which browsers never send to a server -- so this
spins up a one-shot local HTTP server, opens the system browser to
Twitch's authorize page, and serves a tiny page at the redirect URI
whose JS reads location.hash and immediately re-requests itself with
the token as a query string (which *does* reach the server). No
client secret needed for this flow, so it's safe to run from a
desktop app.
"""
from __future__ import annotations

import http.server
import logging
import threading
import urllib.parse
import webbrowser
from typing import Optional

logger = logging.getLogger("chatbot.oauth")

DEFAULT_PORT = 17563
CHAT_SCOPES = ["chat:read", "chat:edit"]
# user:write:chat lets the broadcaster's own token post chat messages via
# the Helix Chat API -- powers the GUI's "chat as streamer" identity option.
# channel:manage:broadcast lets it update the stream title/category --
# powers the Console tab's pencil-icon "update title/game" dialog.
HELIX_SCOPES = ["moderator:read:followers", "user:write:chat", "channel:manage:broadcast"]

_CAPTURE_PAGE = """<!DOCTYPE html><html><head><title>Twitch Auth</title></head>
<body style="font-family:sans-serif;padding:2rem;">
<p id="msg">Finishing sign-in...</p>
<script>
  const hash = new URLSearchParams(window.location.hash.slice(1));
  const token = hash.get("access_token");
  const scope = hash.get("scope") || "";
  const error = hash.get("error");
  if (error) {
    document.getElementById("msg").textContent = "Authorization failed: " + error + ". You can close this window.";
  } else if (token) {
    fetch("/capture?access_token=" + encodeURIComponent(token) + "&scope=" + encodeURIComponent(scope))
      .then(() => { document.getElementById("msg").textContent = "Signed in! You can close this window."; });
  } else {
    document.getElementById("msg").textContent = "No token received. You can close this window.";
  }
</script>
</body></html>"""


class _CaptureHandler(http.server.BaseHTTPRequestHandler):
    result: dict = {}
    done_event: threading.Event = threading.Event()

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/capture":
            params = urllib.parse.parse_qs(parsed.query)
            _CaptureHandler.result = {
                "access_token": params.get("access_token", [""])[0],
                "scope": params.get("scope", [""])[0],
            }
            _CaptureHandler.done_event.set()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(_CAPTURE_PAGE.encode("utf-8"))

    def log_message(self, format: str, *args) -> None:  # silence default stderr logging
        logger.debug("oauth server: " + format, *args)


def authorize(client_id: str, scopes: list[str], port: int = DEFAULT_PORT, timeout: float = 180.0) -> Optional[dict]:
    """Blocks (call from a worker thread, not the GUI thread) until the
    user finishes the browser flow or `timeout` seconds pass. Returns
    {'access_token': str, 'scope': str} or None on timeout/cancel."""
    _CaptureHandler.result = {}
    _CaptureHandler.done_event = threading.Event()

    redirect_uri = f"http://localhost:{port}/"
    server = http.server.HTTPServer(("localhost", port), _CaptureHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "token",
        "scope": " ".join(scopes),
    }
    url = "https://id.twitch.tv/oauth2/authorize?" + urllib.parse.urlencode(params)
    webbrowser.open(url)

    finished = _CaptureHandler.done_event.wait(timeout)
    server.shutdown()
    server_thread.join(timeout=5)

    if not finished or not _CaptureHandler.result.get("access_token"):
        return None
    return dict(_CaptureHandler.result)
