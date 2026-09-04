"""Checks GitHub for a newer LCBot release than the one currently
running. A plain, anonymous GitHub API call -- works with no token
since the LCBot repo is public, and doesn't need one (this is a
read-only check of a public releases list). Silently no-ops on any
failure (no internet, GitHub down, rate-limited, unexpected response)
rather than bothering the user about it -- a failed update check is
never worth interrupting them over, it just means the "new version
available" banner doesn't show up this launch."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Optional

LATEST_RELEASE_URL = "https://api.github.com/repos/Lilcrash15/LCBot/releases/latest"

# Tags look like "v0.1.1" or occasionally "v0.1.1.2" -- match 3 or 4
# dot-separated numbers after an optional leading "v".
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?")


def _parse_version(text: str) -> tuple:
    m = _VERSION_RE.search(text or "")
    if not m:
        return (0,)
    return tuple(int(g) for g in m.groups() if g is not None)


def check_for_update(current_version: str, timeout: float = 5.0) -> Optional[dict]:
    """Returns {'version': 'v0.1.2', 'url': 'https://github.com/.../releases/tag/v0.1.2'}
    if GitHub's latest published release is newer than current_version,
    else None -- including on any network/API failure."""
    try:
        req = urllib.request.Request(
            LATEST_RELEASE_URL, headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        return None

    if not isinstance(data, dict):
        return None
    tag = data.get("tag_name", "")
    if not tag:
        return None
    if _parse_version(tag) > _parse_version(current_version):
        return {"version": tag, "url": data.get("html_url") or LATEST_RELEASE_URL}
    return None
