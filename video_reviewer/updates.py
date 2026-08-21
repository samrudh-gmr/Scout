"""Version and GitHub Release checks for the user-facing Scout app."""
from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from video_reviewer import __version__

_REPOSITORY = "samrudh-gmr/Scout"
_RELEASES_API = f"https://api.github.com/repos/{_REPOSITORY}/releases/latest"
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024


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
    assets = payload.get("assets", [])
    dmg_assets = [
        asset for asset in assets
        if isinstance(asset, dict) and str(asset.get("name", "")).lower().endswith(".dmg")
    ]
    machine = platform.machine().lower()
    preferred = [
        asset for asset in dmg_assets
        if ("arm" in machine and "arm" in str(asset.get("name", "")).lower())
        or (machine in {"x86_64", "amd64"} and any(token in str(asset.get("name", "")).lower() for token in ("x86", "intel")))
    ]
    selected_asset = (preferred or dmg_assets or [None])[0]
    result.update(
        {
            "latest_version": tag.lstrip("v"),
            "update_available": True,
            "release_url": payload.get("html_url") or result["release_url"],
            "download_url": (selected_asset or {}).get("browser_download_url"),
            "download_name": (selected_asset or {}).get("name"),
        }
    )
    return result


def download_and_open_update(info: dict[str, object] | None = None) -> dict[str, object]:
    """Download the latest macOS DMG into Downloads and open it."""
    if sys.platform != "darwin":
        raise RuntimeError("In-app updates are currently available on macOS only.")
    info = info or check_latest_release(timeout=5.0)
    if not info.get("update_available") or not info.get("download_url"):
        raise RuntimeError("No downloadable Scout update is available.")
    version = str(info.get("latest_version") or "latest")
    name = Path(str(info.get("download_name") or f"Scout-{version}.dmg")).name
    if not name.lower().endswith(".dmg"):
        raise RuntimeError("The latest Scout release does not provide a macOS installer.")
    downloads = Path.home() / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    destination = downloads / name
    partial = destination.with_suffix(destination.suffix + ".part")
    request = Request(str(info["download_url"]), headers={"User-Agent": "Scout-App"})
    total = 0
    try:
        with urlopen(request, timeout=30) as response, partial.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > _MAX_DOWNLOAD_BYTES:
                    raise RuntimeError("The Scout installer is unexpectedly large.")
                output.write(chunk)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    subprocess.Popen(["open", str(destination)], start_new_session=True)
    return {"path": str(destination), "version": version}
