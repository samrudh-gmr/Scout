from __future__ import annotations

import json
import hashlib
import math
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


def _artifact_stem(source_path: Path) -> str:
    identity = str(source_path.expanduser().absolute()).encode("utf-8", errors="surrogatepass")
    digest = hashlib.sha256(identity).hexdigest()[:12]
    suffix = source_path.suffix.lower().lstrip(".") or "video"
    return f"{_safe_stem(source_path)}_{suffix}_{digest}"


def create_proxy_path(tmp_dir: Path, source_path: Path) -> Path:
    return tmp_dir / f"{_artifact_stem(source_path)}.proxy.mp4"


def create_frame_dir(tmp_dir: Path, source_path: Path) -> Path:
    return tmp_dir / f"{_artifact_stem(source_path)}_frames"


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
    """Default AI frame count: about one frame per 30s, min 4, max 20."""
    if duration_seconds <= 0:
        return 4
    return max(4, min(20, math.ceil(duration_seconds / 30)))


def sample_timestamps(duration_seconds: float, count: int) -> list[float]:
    """Return deterministic midpoint timestamps across a video."""
    count = max(1, int(count))
    if duration_seconds <= 0:
        return [float(i) for i in range(count)]
    return [round((i + 0.5) * duration_seconds / count, 3) for i in range(count)]


def _clear_old_frames(frame_dir: Path) -> None:
    if not frame_dir.exists():
        return
    for path in frame_dir.glob("frame_*.jpg"):
        path.unlink(missing_ok=True)


def extract_sample_frames(
    source_path: Path,
    frame_dir: Path,
    count: int,
    duration: float = 0.0,
    *,
    max_width: int = 0,
    quality: int = 2,
) -> list[Path]:
    """Extract exact representative frames from the source video.

    Frames are not downscaled by default. Set ``max_width`` for a cost-saving mode.
    ``quality`` maps to ffmpeg's JPEG q:v (2 is high quality, 31 is low quality).
    """
    frame_dir.mkdir(parents=True, exist_ok=True)
    _clear_old_frames(frame_dir)
    outputs: list[Path] = []
    vf: list[str] = []
    if max_width and max_width > 0:
        vf.append(f"scale='min({int(max_width)},iw)':-2")
    for idx, timestamp in enumerate(sample_timestamps(duration, count)):
        output = frame_dir / f"frame_{idx:02d}.jpg"
        cmd = [
            get_ffmpeg_path(),
            "-y",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(source_path),
            "-frames:v",
            "1",
            "-q:v",
            str(max(2, min(31, int(quality)))),
        ]
        if vf:
            cmd.extend(["-vf", ",".join(vf)])
        cmd.append(str(output))
        subprocess.run(cmd, check=True)
        if output.exists():
            outputs.append(output)
    return outputs
