from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _get_bundle_dir() -> Path | None:
    """Return PyInstaller bundle dir if running as a frozen executable."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ""))
    return None


def _try_static_ffmpeg() -> None:
    """Ensure static-ffmpeg binaries are on PATH (downloads on first use)."""
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
    except ImportError:
        pass


def get_ffmpeg_path() -> str:
    bundle = _get_bundle_dir()
    if bundle:
        candidate = bundle / "ffmpeg"
        if candidate.exists():
            return str(candidate)
    app_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else None
    if app_dir:
        candidate = app_dir / "ffmpeg"
        if candidate.exists():
            return str(candidate)
    path = shutil.which("ffmpeg")
    if path:
        return path
    _try_static_ffmpeg()
    path = shutil.which("ffmpeg")
    if path:
        return path
    raise RuntimeError("ffmpeg not found. Install ffmpeg or place it alongside the application.")


def get_ffprobe_path() -> str:
    bundle = _get_bundle_dir()
    if bundle:
        candidate = bundle / "ffprobe"
        if candidate.exists():
            return str(candidate)
    app_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else None
    if app_dir:
        candidate = app_dir / "ffprobe"
        if candidate.exists():
            return str(candidate)
    path = shutil.which("ffprobe")
    if path:
        return path
    _try_static_ffmpeg()
    path = shutil.which("ffprobe")
    if path:
        return path
    raise RuntimeError("ffprobe not found. Install ffprobe or place it alongside the application.")


def require_fftools() -> None:
    try:
        get_ffmpeg_path()
        get_ffprobe_path()
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc


def parse_creation_time(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return ""


def probe_media(path: Path) -> dict[str, str]:
    cmd = [
        get_ffprobe_path(),
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:format_tags=creation_time:stream=width,height",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout or "{}")
    fmt = payload.get("format", {})
    streams = payload.get("streams", [])
    width = ""
    height = ""
    if streams:
        width = str(streams[0].get("width", ""))
        height = str(streams[0].get("height", ""))
    return {
        "capture_time": parse_creation_time(fmt.get("tags", {}).get("creation_time", "")),
        "duration": str(fmt.get("duration", "")),
        "size": str(fmt.get("size", "")),
        "width": width,
        "height": height,
    }


def _safe_stem(source_path: Path) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in source_path.stem).strip("_")


def create_proxy_path(tmp_dir: Path, source_path: Path) -> Path:
    return tmp_dir / f"{_safe_stem(source_path)}.proxy.mp4"


def create_frame_dir(tmp_dir: Path, source_path: Path) -> Path:
    return tmp_dir / f"{_safe_stem(source_path)}_frames"


def run_ffmpeg_proxy(source_path: Path, proxy_path: Path, scale: int) -> None:
    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-i",
        str(source_path),
        "-vf",
        f"scale='min({scale},iw)':-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(proxy_path),
    ]
    subprocess.run(cmd, check=True)


def compute_frame_count(duration_seconds: float) -> int:
    """Return a frame count scaled to video duration: ~1 frame per 30s, min 4, max 20."""
    return max(4, min(20, int(duration_seconds / 30)))


def extract_sample_frames(source_path: Path, frame_dir: Path, count: int, duration: float = 0.0) -> list[Path]:
    frame_dir.mkdir(parents=True, exist_ok=True)
    pattern = frame_dir / "frame_%02d.jpg"
    # Spread frames evenly across the video. interval=1 falls back to 1fps if duration unknown.
    interval = max(1.0, duration / count) if duration > 0 else 1.0
    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-i",
        str(source_path),
        "-vf",
        f"fps=1/{interval:.3f},scale='min(960,iw)':-2",
        "-frames:v",
        str(count),
        str(pattern),
    ]
    subprocess.run(cmd, check=True)
    return sorted(frame_dir.glob("frame_*.jpg"))
