"""Version and GitHub Release checks for the user-facing Scout app."""
from __future__ import annotations

import json
import re
from urllib.request import Request, urlopen

from video_reviewer import __version__

_REPOSITORY = "samrudh-gmr/Scout"
_RELEASES_API = f"https://api.github.com/repos/{_REPOSITORY}/releases/latest"
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.match(value.strip())
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def is_newer_version(candidate: str, current: str = __version__) -> bool:
    """Return whether a release version is newer than the installed version."""
    return _version_tuple(candidate) > _version_tuple(current)


def check_latest_release(timeout: float = 2.0) -> dict[str, object]:
    """Return update metadata without raising on offline or blocked networks."""
    result: dict[str, object] = {
        "current_version": __version__,
        "update_available": False,
        "release_url": f"https://github.com/{_REPOSITORY}/releases/latest",
    }
    request = Request(
        _RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "Scout-App"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS GitHub URL
            payload = json.load(response)
    except Exception:  # noqa: BLE001 - update checks must never interrupt local work
        return result

    if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
        return result
    tag = str(payload.get("tag_name") or "").strip()
    if not tag or not is_newer_version(tag):
        return result
    result.update(
        {
            "latest_version": tag.lstrip("v"),
            "update_available": True,
            "release_url": payload.get("html_url") or result["release_url"],
        }
    )
    return result
