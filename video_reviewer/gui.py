from __future__ import annotations

import copy
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from video_reviewer.config import api_key_hint, delete_api_key, load_api_key, save_api_key, save_correction
from video_reviewer.manifest import (
    manifest_transaction,
    REVIEW_APPROVED,
    read_manifest_csv,
    write_manifest_csv,
)
from video_reviewer.sop import build_proposed_name
from video_reviewer.workflow import assign_sequences_by_name

logger = logging.getLogger("video_renamer")

_STATIC_DIR = Path(__file__).parent / "static"
_DEFAULT_MANIFEST_PATH = Path.home() / ".video-renamer" / "manifest.csv"


def _safe_artifact(path: Path, allowed_suffixes: set[str]) -> bool:
    try:
        return (
            path.suffix.lower() in allowed_suffixes
            and path.exists()
            and path.is_file()
            and not path.is_symlink()
        )
    except OSError:
        return False


def _pick_directory_sync() -> subprocess.CompletedProcess[str]:
    """Run a desktop picker with a hard timeout; caller supplies the worker thread."""
    if sys.platform.startswith("linux"):
        executable = shutil.which("zenity")
        if not executable:
            raise FileNotFoundError("zenity is not installed; install it or paste the folder path")
        command = [executable, "--file-selection", "--directory", "--title=Select video folder"]
    elif sys.platform == "win32":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$d.Description = 'Select folder';"
            "[void]$d.ShowDialog();"
            "Write-Output $d.SelectedPath"
        )
        command = ["powershell", "-NoProfile", "-Command", ps]
    elif sys.platform == "darwin":
        command = ["osascript", "-e", 'POSIX path of (choose folder with prompt "Select folder")']
    else:
        raise OSError(f"no native folder picker is configured for {sys.platform}")
    env = os.environ.copy()
    # Desktop terminals installed through Snap/Flatpak can inject loader paths
    # that make host-native pickers load incompatible libc/GTK libraries.
    for name in (
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "GTK_PATH",
        "GIO_EXTRA_MODULES",
        "GI_TYPELIB_PATH",
    ):
        env.pop(name, None)
    return subprocess.run(command, capture_output=True, text=True, timeout=20, check=False, env=env)

def create_app(manifest_path: Path | None = None) -> FastAPI:
    """Create the local ASGI app.

    The optional path keeps ``uvicorn video_reviewer.gui:create_app --factory``
    usable while the CLI can still open a caller-selected manifest.
    """
    manifest_path = (manifest_path or _DEFAULT_MANIFEST_PATH).expanduser().resolve()
    if not manifest_path.exists():
        write_manifest_csv(manifest_path, [])
    app = FastAPI()
    prepare_lock = threading.Lock()

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
                "industry": row.industry,
                "client_or_location": row.client_or_location,
                "sequence": row.sequence,
                "proposed_name": row.proposed_name,
                "ai_confidence": confidence,
                "ai_rationale": row.ai_rationale,
                "ai_flags": row.ai_flags,
                "review_status": row.review_status,
                "source_path_ext": Path(row.source_path).suffix,
                "frame_count": len(frame_paths),
                "frames": [Path(frame).name for frame in frame_paths],
            })
        return JSONResponse({
            "rows": rows_data,
            "threshold": 0.78,
            "manifest_path": str(manifest_path),
            "default_model": "",
        })


    # ── /api/browse-dir: cross-platform in-app fallback ─────────────────────
    @app.get("/api/browse-dir")
    async def api_browse_dir(path: str = Query(default="")) -> JSONResponse:
        target = Path(path).expanduser() if path.strip() else Path.home()
        try:
            target = target.resolve(strict=True)
        except (OSError, RuntimeError):
            return JSONResponse({"error": "That folder does not exist or cannot be opened."}, status_code=404)
        if not target.is_dir():
            return JSONResponse({"error": "The selected path is not a folder."}, status_code=422)
        try:
            directories = sorted(
                (
                    entry for entry in target.iterdir()
                    if entry.is_dir() and not entry.is_symlink() and not entry.name.startswith(".")
                ),
                key=lambda entry: entry.name.casefold(),
            )
        except (PermissionError, OSError):
            return JSONResponse({"error": "Permission denied while opening that folder."}, status_code=403)
        parent = target.parent if target.parent != target else target
        return JSONResponse({
            "path": str(target),
            "parent": str(parent),
            "dirs": [{"name": entry.name, "path": str(entry)} for entry in directories],
        })

    # ── /api/pick-dir: optional native desktop picker ────────────────────────
    @app.get("/api/pick-dir")
    async def api_pick_dir() -> JSONResponse:
        import anyio
        try:
            result = await anyio.to_thread.run_sync(_pick_directory_sync)
        except TimeoutError:
            return JSONResponse({"error": "Folder picker timed out. Close other dialogs and try again, or paste the path."}, status_code=504)
        except (FileNotFoundError, OSError) as exc:
            logger.warning("Folder picker failed: %s", exc)
            return JSONResponse({"error": f"Could not open a native folder picker ({exc}). Paste the path instead."}, status_code=503)
        if result.returncode == 1:
            return JSONResponse({"path": None, "cancelled": True})
        if result.returncode != 0:
            detail = result.stderr.strip() or "native picker returned an error"
            return JSONResponse({"error": f"Folder picker failed: {detail}. Paste the path instead."}, status_code=503)
        selected = result.stdout.strip()
        path = "/" if selected == "/" else selected.rstrip("/") or None
        return JSONResponse({"path": path, "cancelled": path is None})

    # ── /api/video ──────────────────────────────────────────────────────────
    @app.get("/api/video/{index}")
    async def serve_video(index: int) -> FileResponse:
        rows = read_manifest_csv(manifest_path)
        if index < 0 or index >= len(rows):
            return JSONResponse({"error": "not found"}, status_code=404)
        raw_proxy = rows[index].proxy_path
        proxy = Path(raw_proxy) if raw_proxy else None
        if not proxy or not _safe_artifact(proxy, {".mp4"}):
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
        if not _safe_artifact(frame_path, {".jpg", ".jpeg", ".png"}):
            return JSONResponse({"error": "frame not found"}, status_code=404)
        suffix = frame_path.suffix.lower()
        media_type = "image/png" if suffix == ".png" else "image/jpeg"
        return FileResponse(frame_path, media_type=media_type)

    # ── /api/save ───────────────────────────────────────────────────────────
    @app.post("/api/save")
    async def save(body: dict) -> JSONResponse:
        try:
            edited = {int(item["index"]): item for item in body.get("rows", [])}
        except (KeyError, TypeError, ValueError):
            return JSONResponse({"error": "Each edited row must include a valid integer index."}, status_code=422)
        with manifest_transaction(manifest_path):
            original_rows = read_manifest_csv(manifest_path)
            rows = copy.deepcopy(original_rows)
            corrections: list[tuple] = []
            for i, row in enumerate(rows):
                edit = edited.get(i)
                if not edit or not edit.get("checked"):
                    continue
                new_desc = edit.get("description", row.description)
                new_industry = edit.get("industry", row.industry)
                new_client = edit.get("client_or_location", row.client_or_location)
                if row.description and (new_desc != row.description or new_industry != row.industry or new_client != row.client_or_location):
                    corrections.append((Path(row.source_path).name, row.description, row.client_or_location, new_desc, new_client))
                row.description = new_desc
                row.industry = new_industry
                row.client_or_location = new_client
                row.year_month = edit.get("year_month", row.year_month)
                row.review_status = REVIEW_APPROVED

            # The suffix distinguishes otherwise-identical names; it must not
            # reflect a clip's position in the imported folder.
            assign_sequences_by_name(rows)
            for i, row in enumerate(rows):
                if i not in edited or not edited[i].get("checked"):
                    continue
                try:
                    row.proposed_name = build_proposed_name(row)
                except ValueError as exc:
                    return JSONResponse({"error": f"Row {i} ({Path(row.source_path).name}): {exc}"}, status_code=422)
            for source_name, old_desc, old_client, new_desc, new_client in corrections:
                save_correction(
                    source_name=source_name,
                    ai_fields={"description": old_desc, "client_or_location": old_client},
                    corrected_fields={"description": new_desc, "client_or_location": new_client},
                )
            write_manifest_csv(manifest_path, rows)
        return JSONResponse({"ok": True})

    # ── /api/run-prepare ────────────────────────────────────────────────────
    @app.post("/api/run-prepare")
    async def run_prepare_route(body: dict) -> JSONResponse:
        import anyio
        from video_reviewer.workflow import build_prepare_manifest
        if not prepare_lock.acquire(blocking=False):
            return JSONResponse({"error": "A prepare job is already running. Wait for it to finish."}, status_code=409)
        try:
            input_dir = Path(body["input_dir"]).expanduser().resolve()
            rows = await anyio.to_thread.run_sync(
                lambda: build_prepare_manifest(
                    input_dir=input_dir,
                    year_month=body.get("year_month", ""),
                    client_or_location=body.get("client_or_location", ""),
                    start_seq=int(body.get("start_seq", 1)),
                    tmp_dir=Path(body.get("tmp_dir", "tmp")).expanduser().resolve(),
                    proxy_scale=int(body.get("proxy_scale", 1280)),
                    sample_count=int(body.get("sample_count", 0)),
                )
            )
            if not rows:
                return JSONResponse(
                    {"error": "No supported video files were found. The existing batch was left unchanged."},
                    status_code=422,
                )
            with manifest_transaction(manifest_path):
                write_manifest_csv(manifest_path, rows)
            return JSONResponse({
                "ok": True,
                "message": f"Prepared {len(rows)} file(s). Manifest written to {manifest_path}.",
            })
        except Exception as exc:
            logger.exception("Prepare failed")
            return JSONResponse({"error": f"Prepare failed: {type(exc).__name__}. Check the folder and available disk space."}, status_code=500)
        finally:
            prepare_lock.release()

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
            if action["status"] in {"would_rename", "would_copy"}:
                verb = "copy" if action["status"] == "would_copy" else "rename"
                lines.append(f"Would {verb}: {action['source']} -> {action['target']}")
            elif action["status"] in {"renamed", "copied", "already_named"}:
                lines.append(f"{action['status'].replace('_', ' ').title()}: {action['target']}")
        return JSONResponse({
            "ok": result.ok,
            "output": "\n".join(lines),
            "return_code": 0 if result.ok else 1,
        })

    # ── /api/ai/status ──────────────────────────────────────────────────────
    @app.get("/api/ai/status")
    async def ai_status(provider: str = Query("gemini")) -> JSONResponse:
        from video_reviewer.ai_review import available_providers, pending_indices, provider_status

        stored = load_api_key(provider)
        status = provider_status(provider, api_key=stored or None)
        return JSONResponse({
            "saved_key": bool(stored),
            "key_hint": api_key_hint(provider),
            "provider": status.provider_id,
            "display_name": status.display_name,
            "available": status.available,
            "needs_key": not status.has_key,
            "message": status.message,
            "default_model": status.default_model,
            "cheap_model": status.cheap_model,
            "accurate_model": status.accurate_model,
            "models": list(status.models),
            "env_key_names": list(status.env_key_names),
            "providers": available_providers(),
            "pending": pending_indices(manifest_path),
        })

    @app.get("/api/gemini/status")
    async def gemini_status() -> JSONResponse:
        return await ai_status("gemini")

    # ── /api/naming-guide ───────────────────────────────────────────────────
    # The guide steers every provider. Editing it is how you correct naming
    # without touching code, so it is editable from the app as well as on disk.
    @app.get("/api/naming-guide")
    async def get_naming_guide() -> JSONResponse:
        from video_reviewer.naming_guide import GUIDE_PATH, load_guide

        return JSONResponse({"path": str(GUIDE_PATH), "text": load_guide()})

    @app.post("/api/naming-guide")
    async def put_naming_guide(body: dict) -> JSONResponse:
        from video_reviewer.naming_guide import GUIDE_PATH, ensure_guide

        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            return JSONResponse({"error": "The naming guide cannot be empty."}, status_code=422)
        ensure_guide()
        GUIDE_PATH.write_text(text.strip() + "\n", encoding="utf-8")
        return JSONResponse({"ok": True, "path": str(GUIDE_PATH)})

    @app.post("/api/naming-guide/reset")
    async def reset_naming_guide() -> JSONResponse:
        from video_reviewer.naming_guide import load_guide, reset_guide

        path = reset_guide()
        return JSONResponse({"ok": True, "path": str(path), "text": load_guide()})

    # ── /api/settings/key ───────────────────────────────────────────────────
    # Keys are held server-side in a 0600 file. The browser never receives one
    # back — only whether a key is stored and its masked last four characters.
    @app.post("/api/settings/key")
    async def save_key(body: dict) -> JSONResponse:
        provider = (body.get("provider") or "").strip()
        api_key = (body.get("api_key") or "").strip()
        if not provider:
            return JSONResponse({"error": "Choose a provider before saving a key."}, status_code=422)
        if not api_key:
            return JSONResponse({"error": "Paste a key before saving."}, status_code=422)
        save_api_key(provider, api_key)
        return JSONResponse({"ok": True, "saved_key": True, "key_hint": api_key_hint(provider)})

    @app.delete("/api/settings/key")
    async def forget_key(provider: str = Query("")) -> JSONResponse:
        if not provider:
            return JSONResponse({"error": "Choose a provider before removing a key."}, status_code=422)
        removed = delete_api_key(provider)
        return JSONResponse({"ok": True, "removed": removed, "saved_key": False, "key_hint": ""})

    # ── /api/ai/review ──────────────────────────────────────────────────────
    @app.post("/api/ai/review")
    async def ai_review_route(body: dict) -> JSONResponse:
        import anyio

        from video_reviewer.ai_review import (
            AiReviewError,
            ReviewPolicy,
            pending_indices,
            review_rows_with_ai,
        )

        indices = body.get("indices") if "indices" in body else None
        provider = (body.get("provider") or "gemini").strip()
        model = (body.get("model") or "").strip() or None
        api_key = (body.get("api_key") or "").strip() or load_api_key(provider) or None
        if body.get("remember_key") and (body.get("api_key") or "").strip():
            save_api_key(provider, body["api_key"].strip())
        policy = ReviewPolicy.from_preset(body.get("preset") or "balanced")
        if indices is None:
            indices = pending_indices(manifest_path)
        elif not indices:
            return JSONResponse({"error": "Select at least one video to review."}, status_code=422)
        try:
            indices = [int(i) for i in indices]
        except (TypeError, ValueError):
            return JSONResponse({"error": "Review indices must be integers."}, status_code=422)

        try:
            # Provider SDK calls are blocking; run them off the
            # event loop so the GUI stays responsive.
            results = await anyio.to_thread.run_sync(
                lambda: review_rows_with_ai(
                    manifest_path,
                    indices,
                    provider_id=provider,
                    model=model,
                    api_key=api_key,
                    policy=policy,
                )
            )
        except AiReviewError as exc:
            return JSONResponse({"error": str(exc), "category": exc.category.value}, status_code=400)
        except Exception as exc:  # noqa: BLE001 - surface to the GUI
            logger.exception("AI review failed")
            return JSONResponse(
                {"error": "AI review failed unexpectedly. Check the provider settings and try again."},
                status_code=500,
            )

        return JSONResponse({
            "ok": True,
            "approved": sum(1 for r in results if r.ok),
            "total": len(results),
            "results": [asdict(r) for r in results],
        })

    @app.post("/api/gemini/review")
    async def gemini_review_route(body: dict) -> JSONResponse:
        body = dict(body)
        body["provider"] = "gemini"
        return await ai_review_route(body)

    # ── /api/ai/chat ────────────────────────────────────────────────────────
    @app.post("/api/ai/chat")
    async def ai_chat_route(body: dict) -> JSONResponse:
        import anyio

        from video_reviewer.ai_review import AiReviewError
        from video_reviewer.ai_review.chat import chat_about_row

        provider = (body.get("provider") or "gemini").strip()
        model = (body.get("model") or "").strip() or None
        api_key = (body.get("api_key") or "").strip() or load_api_key(provider) or None
        messages = body.get("messages")
        frame_notes = str(body.get("frame_notes") or "").strip()
        try:
            index = int(body.get("index"))
        except (TypeError, ValueError):
            return JSONResponse({"error": "Pick a clip before asking about it."}, status_code=422)
        if not isinstance(messages, list) or not messages:
            return JSONResponse({"error": "Ask a question first."}, status_code=422)

        try:
            reply = await anyio.to_thread.run_sync(
                lambda: chat_about_row(
                    manifest_path, index, messages,
                    provider_id=provider, model=model, api_key=api_key,
                    frame_notes=frame_notes,
                )
            )
        except AiReviewError as exc:
            return JSONResponse({"error": str(exc), "category": exc.category.value}, status_code=400)
        except Exception:  # noqa: BLE001 - surface to the GUI
            logger.exception("AI chat failed")
            return JSONResponse({"error": "The assistant failed unexpectedly. Try again."}, status_code=500)

        return JSONResponse({
            "message": reply.message,
            "set_fields": reply.set_fields,
            "set_batch_fields": reply.set_batch_fields,
            "remembered": reply.remembered,
            "frame_notes": reply.frame_notes,
            "need_frames": reply.need_frames,
        })

    # ── legacy page routes ──────────────────────────────────────────────────
    # The standalone AI page is gone; AI naming is step 2 of the one app.
    @app.get("/ai-review")
    async def ai_page() -> RedirectResponse:
        return RedirectResponse("/#/name", status_code=307)

    @app.get("/gemini")
    async def gemini_page() -> RedirectResponse:
        return RedirectResponse("/#/name", status_code=307)

    # ── SPA: serve static build ─────────────────────────────────────────────
    if _STATIC_DIR.exists() and any(_STATIC_DIR.iterdir()):
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")

    return app


def launch_gui(manifest_path: Path, host: str, port: int) -> None:
    import webbrowser

    import uvicorn

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "Scout has no remote authentication and can only bind to localhost. "
            "Use --host 127.0.0.1."
        )
    selected_port = _choose_port(host, port)
    if selected_port != port:
        print(f"Port {port} is already in use; starting Scout on port {selected_port} instead.")
    url = f"http://{host}:{selected_port}/ai-review"
    print(f"Opening 🐾 Scout: {url}")
    # Safari is notably unforgiving when asked to open a localhost address
    # before the listener exists: the first interaction can land on its error
    # page instead of the app. Start Uvicorn first, wait for the socket, then
    # hand the address to the system browser.
    config = uvicorn.Config(create_app(manifest_path), host=host, port=selected_port)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="video-renamer-server", daemon=True)
    thread.start()
    for _ in range(50):
        try:
            with socket.create_connection((host, selected_port), timeout=0.1):
                break
        except OSError:
            thread.join(0.1)
            if not thread.is_alive():
                raise RuntimeError("Video Renamer server stopped before the browser could open.")
    else:
        server.should_exit = True
        thread.join(1)
        raise RuntimeError("Video Renamer server did not start within 5 seconds.")
    webbrowser.open(url)
    thread.join()


def _choose_port(host: str, preferred_port: int, attempts: int = 20) -> int:
    for candidate in range(preferred_port, preferred_port + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError(
        f"Could not find an available port from {preferred_port} to {preferred_port + attempts - 1}. "
        "Close another Video Renamer window/server or pass --port with a free port."
    )
