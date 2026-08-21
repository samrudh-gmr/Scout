from __future__ import annotations

import json
from pathlib import Path


def test_version_comparison_accepts_release_tags() -> None:
    from video_reviewer.updates import is_newer_version

    assert is_newer_version("v0.3.0", "0.2.0")
    assert is_newer_version("0.2.1", "0.2.0")
    assert not is_newer_version("v0.2.0", "0.2.0")
    assert not is_newer_version("v0.1.9", "0.2.0")


def test_update_check_is_safe_when_offline(monkeypatch) -> None:
    from video_reviewer import __version__, updates

    def fail(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(updates, "urlopen", fail)
    result = updates.check_latest_release()

    assert result["current_version"] == __version__
    assert result["update_available"] is False
    assert str(result["release_url"]).endswith("/releases/latest")


def test_update_check_returns_new_release(monkeypatch) -> None:
    from video_reviewer import updates

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {
                    "tag_name": "v0.3.0",
                    "html_url": "https://github.com/samrudh-gmr/Scout/releases/tag/v0.3.0",
                    "assets": [
                        {
                            "name": "Scout-0.3.0-macos-ARM64.dmg",
                            "browser_download_url": "https://example.test/Scout.dmg",
                        }
                    ],
                    "draft": False,
                    "prerelease": False,
                }
            ).encode()

    monkeypatch.setattr(updates, "urlopen", lambda *args, **kwargs: FakeResponse())
    result = updates.check_latest_release()

    assert result["update_available"] is True
    assert result["latest_version"] == "0.3.0"
    assert str(result["release_url"]).endswith("/v0.3.0")
    assert result["download_url"] == "https://example.test/Scout.dmg"


def test_download_and_open_update_writes_dmg_and_opens_it(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer import updates

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __init__(self):
            self._sent = False

        def read(self, size=-1):
            if self._sent:
                return b""
            self._sent = True
            return b"dmg-bytes"

    monkeypatch.setattr(updates.sys, "platform", "darwin")
    monkeypatch.setattr(updates.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(updates, "urlopen", lambda *args, **kwargs: FakeResponse())
    opened: list[list[str]] = []
    monkeypatch.setattr(updates.subprocess, "Popen", lambda command, **kwargs: opened.append(command))

    result = updates.download_and_open_update(
        {
            "update_available": True,
            "latest_version": "0.3.0",
            "download_name": "Scout-0.3.0-macos-ARM64.dmg",
            "download_url": "https://example.test/Scout.dmg",
        }
    )

    destination = tmp_path / "Downloads" / "Scout-0.3.0-macos-ARM64.dmg"
    assert result["version"] == "0.3.0"
    assert destination.read_bytes() == b"dmg-bytes"
    assert opened == [["open", str(destination)]]


def test_app_info_endpoint_returns_update_metadata(monkeypatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from video_reviewer import __version__, gui
    from video_reviewer.gui import create_app

    monkeypatch.setattr(
        gui,
        "check_latest_release",
        lambda: {"current_version": __version__, "update_available": False},
    )
    response = TestClient(create_app(tmp_path / "manifest.csv")).get("/api/app-info")

    assert response.status_code == 200
    assert response.json()["current_version"] == __version__
