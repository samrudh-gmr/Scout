from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from video_reviewer.config import save_correction
from video_reviewer.manifest import (
    REVIEW_APPROVED,
    read_manifest_csv,
    write_manifest_csv,
)
from video_reviewer.sop import build_proposed_name

logger = logging.getLogger("video_renamer")

_STATIC_DIR = Path(__file__).parent / "static"


def create_app(manifest_path: Path) -> FastAPI:
    app = FastAPI()

    # ── /api/rows ──────────────────────────────────────────────────────────
    @app.get("/api/rows")
    async def api_rows() -> JSONResponse:
        rows = read_manifest_csv(manifest_path)
        rows_data = []
        for i, row in enumerate(rows):
            try:
                confidence = float(row.ai_confidence)
            except (ValueError, TypeError):
                confidence = 0.0
            frame_paths = [f for f in (row.sample_frames or "").split("|") if f]
            rows_data.append({
                "index": i,
                "source_name": Path(row.source_path).name,
                "year_month": row.year_month,
                "description": row.description,
                "client_or_location": row.client_or_location,
                "sequence": row.sequence,
                "proposed_name": row.proposed_name,
                "ai_confidence": confidence,
                "ai_rationale": row.ai_rationale,
                "ai_flags": row.ai_flags,
                "review_status": row.review_status,
                "source_path_ext": Path(row.source_path).suffix,
                "frame_count": len(frame_paths),
            })
        return JSONResponse({
            "rows": rows_data,
            "threshold": 0.75,
            "manifest_path": str(manifest_path),
            "default_model": "",
        })

    # ── /api/list-dir ───────────────────────────────────────────────────────
    @app.get("/api/list-dir")
    async def api_list_dir(path: str = Query(default="/")) -> JSONResponse:
        target = Path(path).expanduser().resolve()
        if not target.exists() or not target.is_dir():
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            entries = sorted(target.iterdir(), key=lambda p: p.name.lower())
            dirs = [e.name for e in entries if e.is_dir() and not e.name.startswith(".")]
        except PermissionError:
            return JSONResponse({"error": "permission denied"}, status_code=403)
        parent = str(target.parent) if target != target.parent else str(target)
        return JSONResponse({"path": str(target), "parent": parent, "dirs": dirs})

    # ── /api/pick-dir ───────────────────────────────────────────────────────
    @app.get("/api/pick-dir")
    async def api_pick_dir() -> JSONResponse:
        import sys as _sys
        path: str | None = None
        if _sys.platform == "win32":
            import subprocess as _sp
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
                "$d.Description = 'Select folder';"
                "[void]$d.ShowDialog();"
                "Write-Output $d.SelectedPath"
            )
            result = _sp.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True,
            )
            path = result.stdout.strip() or None
        elif _sys.platform == "darwin":
            import subprocess as _sp
            result = _sp.run(
                ["osascript", "-e", 'POSIX path of (choose folder with prompt "Select folder")'],
                capture_output=True, text=True,
            )
            path = result.stdout.strip().rstrip("/") or None
        else:
            try:
                import tkinter as _tk
                from tkinter import filedialog as _fd
                root = _tk.Tk()
                root.withdraw()
                root.wm_attributes("-topmost", True)
                path = _fd.askdirectory(parent=root) or None
                root.destroy()
            except Exception:
                path = None
        return JSONResponse({"path": path})

    # ── /api/video ──────────────────────────────────────────────────────────
    @app.get("/api/video/{index}")
    async def serve_video(index: int) -> FileResponse:
        rows = read_manifest_csv(manifest_path)
        if index < 0 or index >= len(rows):
            return JSONResponse({"error": "not found"}, status_code=404)
        proxy = Path(rows[index].proxy_path)
        if not proxy.exists():
            return JSONResponse({"error": "proxy not found"}, status_code=404)
        return FileResponse(proxy, media_type="video/mp4")

    # ── /api/frame ──────────────────────────────────────────────────────────
    @app.get("/api/frame/{row_index}/{frame_index}")
    async def serve_frame(row_index: int, frame_index: int) -> FileResponse:
        rows = read_manifest_csv(manifest_path)
        if row_index < 0 or row_index >= len(rows):
            return JSONResponse({"error": "not found"}, status_code=404)
        frames = [f for f in (rows[row_index].sample_frames or "").split("|") if f]
        if frame_index < 0 or frame_index >= len(frames):
            return JSONResponse({"error": "not found"}, status_code=404)
        frame_path = Path(frames[frame_index])
        if not frame_path.exists():
            return JSONResponse({"error": "frame not found"}, status_code=404)
        suffix = frame_path.suffix.lower()
        media_type = "image/png" if suffix == ".png" else "image/jpeg"
        return FileResponse(frame_path, media_type=media_type)

    # ── /api/save ───────────────────────────────────────────────────────────
    @app.post("/api/save")
    async def save(body: dict) -> JSONResponse:
        edited = {item["index"]: item for item in body.get("rows", [])}
        rows = read_manifest_csv(manifest_path)
        for i, row in enumerate(rows):
            if i not in edited:
                continue
            edit = edited[i]
            if not edit.get("checked"):
                continue
            new_desc = edit.get("description", row.description)
            new_client = edit.get("client_or_location", row.client_or_location)
            if row.description and (new_desc != row.description or new_client != row.client_or_location):
                save_correction(
                    source_name=Path(row.source_path).name,
                    ai_fields={"description": row.description, "client_or_location": row.client_or_location},
                    corrected_fields={"description": new_desc, "client_or_location": new_client},
                )
            row.description = new_desc
            row.client_or_location = new_client
            row.year_month = edit.get("year_month", row.year_month)
            row.review_status = REVIEW_APPROVED
            try:
                row.proposed_name = build_proposed_name(row)
            except ValueError as exc:
                return JSONResponse(
                    {"error": f"Row {i} ({Path(row.source_path).name}): {exc}"},
                    status_code=422,
                )
        write_manifest_csv(manifest_path, rows)
        return JSONResponse({"ok": True})

    # ── /api/run-prepare ────────────────────────────────────────────────────
    @app.post("/api/run-prepare")
    async def run_prepare_route(body: dict) -> JSONResponse:
        from video_reviewer.workflow import build_prepare_manifest
        try:
            rows = build_prepare_manifest(
                input_dir=Path(body["input_dir"]).resolve(),
                year_month=body.get("year_month", ""),
                start_seq=int(body.get("start_seq", 1)),
                tmp_dir=Path(body.get("tmp_dir", "tmp")).resolve(),
                proxy_scale=int(body.get("proxy_scale", 1280)),
                sample_count=int(body.get("sample_count", 0)),
            )
            write_manifest_csv(manifest_path, rows)
            return JSONResponse({
                "ok": True,
                "message": f"Prepared {len(rows)} file(s). Manifest written to {manifest_path}.",
            })
        except Exception as exc:
            logger.exception("Prepare failed")
            return JSONResponse({"error": f"Prepare failed: {exc}"}, status_code=500)

    # ── /api/run-apply ──────────────────────────────────────────────────────
    @app.post("/api/run-apply")
    async def run_apply_route(body: dict) -> JSONResponse:
        from video_reviewer.workflow import apply_manifest
        output_dir = Path(body["output_dir"]).resolve() if body.get("output_dir") else None
        dry_run = bool(body.get("dry_run", False))
        result = apply_manifest(
            manifest_path=manifest_path,
            output_dir=output_dir,
            dry_run=dry_run,
        )
        lines = list(result.errors)
        for action in result.actions:
            if action["status"] == "would_rename":
                lines.append(f"Would rename: {action['source']} -> {action['target']}")
            elif action["status"] == "renamed":
                lines.append(f"Renamed: {action['target']}")
        return JSONResponse({
            "ok": result.ok,
            "output": "\n".join(lines),
            "return_code": 0 if result.ok else 1,
        })

    # ── SPA: serve static build ─────────────────────────────────────────────
    if _STATIC_DIR.exists() and any(_STATIC_DIR.iterdir()):
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")

    return app


def launch_gui(manifest_path: Path, host: str, port: int) -> None:
    import webbrowser
    import uvicorn

    webbrowser.open(f"http://{host}:{port}")
    uvicorn.run(create_app(manifest_path), host=host, port=port)
