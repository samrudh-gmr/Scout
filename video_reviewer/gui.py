from __future__ import annotations

import logging
from pathlib import Path

from dataclasses import asdict

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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

_AI_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>AI Review — Video Renamer</title>
<style>
  :root { color-scheme: light dark; --accent:#4f46e5; --ok:#16a34a; --warn:#d97706; --err:#dc2626; }
  body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; margin:0; background:Canvas; color:CanvasText; }
  main { max-width:1180px; margin:0 auto; padding:28px; }
  h1 { font-size:1.75rem; margin:0 0 6px; }
  h2 { font-size:1.05rem; margin:0 0 10px; }
  .sub { opacity:.72; margin:0 0 22px; }
  .cards { display:grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap:14px; margin-bottom:18px; }
  .card { border:1px solid #8884; border-radius:16px; padding:16px; background:color-mix(in srgb, Canvas 92%, CanvasText 8%); }
  .steps { display:flex; flex-wrap:wrap; gap:8px; margin:14px 0 22px; }
  .step { border:1px solid #8884; border-radius:999px; padding:6px 10px; font-size:.85rem; opacity:.78; }
  .step.active { background:var(--accent); color:white; opacity:1; border-color:var(--accent); }
  .bar { display:flex; flex-wrap:wrap; gap:12px; align-items:end; margin:14px 0; }
  .field { display:flex; flex-direction:column; gap:5px; font-size:.82rem; font-weight:600; }
  input, select { padding:9px 10px; border:1px solid #8885; border-radius:9px; font:inherit; min-width:190px; background:Canvas; color:CanvasText; }
  button { padding:10px 15px; border:0; border-radius:10px; background:var(--accent); color:#fff; font:inherit; font-weight:650; cursor:pointer; }
  button.secondary { background:#6b7280; } button:disabled { opacity:.5; cursor:not-allowed; }
  #status { margin:12px 0 16px; padding:12px 14px; border:1px solid #8884; border-radius:12px; }
  .err { color:var(--err); } .ok { color:var(--ok); } .warn { color:var(--warn); } .muted { opacity:.68; }
  .privacy { font-size:.88rem; line-height:1.4; }
  .pill { display:inline-block; padding:2px 8px; border-radius:999px; font-size:.75rem; font-weight:650; }
  .pill.pending { background:#f59e0b33; } .pill.approved { background:#16a34a33; }
  .pill.needs_review { background:#f59e0b55; } .pill.blocked,.pill.missing { background:#dc262633; }
  .pill.working { background:#4f46e533; }
  .grid { display:grid; gap:12px; }
  .row { display:grid; grid-template-columns:auto 96px 1fr; gap:12px; border:1px solid #8883; border-radius:14px; padding:12px; align-items:start; }
  .thumbs { display:grid; grid-template-columns:repeat(2, 44px); gap:4px; }
  .thumbs img { width:44px; height:34px; object-fit:cover; border-radius:6px; border:1px solid #8884; }
  .meta { display:grid; gap:4px; }
  .name { font-weight:700; word-break:break-word; }
  .result { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:8px; font-size:.88rem; margin-top:6px; }
  details { margin-top:8px; } summary { cursor:pointer; }
</style>
</head>
<body><main>
  <h1>AI Review</h1>
  <p class="sub">Guided review for nontechnical users: prepare videos, send representative frames to an API provider, confirm uncertain rows, preview, then rename.</p>
  <div class="steps"><span class="step active">1 AI setup</span><span class="step">2 choose videos</span><span class="step">3 review uncertain</span><span class="step">4 preview/apply in main app</span></div>

  <div class="cards">
    <section class="card"><h2>Provider</h2><p class="muted">Gemini is enabled now; the backend is provider-agnostic so more APIs can be added cleanly.</p><div id="providers"></div></section>
    <section class="card privacy"><h2>Privacy + cost</h2><p>External API review sends only sampled frames and metadata for selected rows. API keys are used for the request only and are not written to the manifest.</p><p id="estimate" class="muted">Choose a preset to see the frame budget.</p></section>
    <section class="card"><h2>Recommended defaults</h2><p class="muted">Use <b>Balanced</b> first. It keeps full-resolution sampled frames, limits frames per video, and sends uncertain rows to manual review instead of guessing.</p></section>
  </div>

  <div id="status">Checking AI provider…</div>

  <div class="bar">
    <label class="field">Provider
      <select id="provider"><option value="gemini">Gemini API</option></select>
    </label>
    <label class="field">API key
      <input id="apikey" type="password" placeholder="paste key, or set GEMINI_API_KEY" autocomplete="off" />
    </label>
    <label class="field">Preset
      <select id="preset"><option value="fast">Fast / cheapest</option><option value="balanced" selected>Balanced</option><option value="accurate">Most accurate</option></select>
    </label>
    <details><summary>Advanced</summary><label class="field">Model override<input id="model" placeholder="provider default" /></label></details>
    <button id="reviewSelected">Review selected</button>
    <button id="reviewAll" class="secondary">Review all pending</button>
    <button id="refresh" class="secondary">Refresh</button>
  </div>

  <div class="grid" id="rows"></div>
</main>
<script>
const $ = (id) => document.getElementById(id);
let pending = new Set(); let allRows = [];
function esc(s){ return String(s||"").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function pill(status){ const cls=["pending","approved","needs_review","blocked","missing","working"].includes(status)?status:"muted"; return `<span class="pill ${cls}">${esc(status)}</span>`; }
function statusMsg(text, cls){ const el=$("status"); el.textContent=text; el.className=cls||""; }
function selectedIndices(){ return Array.from(document.querySelectorAll(".rowcheck:checked")).map(c=>Number(c.value)); }
function presetFrames(){ return {fast:6, balanced:12, accurate:16}[$("preset").value] || 12; }
function updateEstimate(){ const n = pending.size; const f = presetFrames(); const total=n*f; const tier= total<=60?'low':total<=250?'medium':'high'; $("estimate").textContent = `${n} pending video(s) × up to ${f} frames = up to ${total} images (${tier} cost tier).`; }
async function loadStatus(){
  const provider=$("provider").value;
  const [statusRes, rowsRes] = await Promise.all([
    fetch(`/api/ai/status?provider=${encodeURIComponent(provider)}`).then(r=>r.json()),
    fetch("/api/rows").then(r=>r.json()),
  ]);
  pending = new Set(statusRes.pending || []); allRows = rowsRes.rows || [];
  $("reviewSelected").disabled = !statusRes.available;
  $("reviewAll").disabled = !statusRes.available;
  $("providers").innerHTML = (statusRes.providers||[]).map(p=>`<div>${esc(p.display_name)} <span class="muted">default ${esc(p.default_model)}</span></div>`).join("");
  if(!statusRes.available) statusMsg(statusRes.message, "err");
  else if(statusRes.needs_key) statusMsg(statusRes.message, "warn");
  else statusMsg(`${statusRes.display_name || 'AI provider'} ready — ${pending.size} video(s) pending.`, "ok");
  updateEstimate(); renderRows(allRows);
}
function renderRows(rows){ const host=$("rows"); host.innerHTML=""; for(const row of rows){
  const checkable = pending.has(row.index);
  const frames = (row.frames || []).slice(0,4).map((_,i)=>`<img alt="frame ${i+1}" src="/api/frame/${row.index}/${i}" loading="lazy"/>`).join("");
  const div=document.createElement("div"); div.className="row"; div.dataset.index=row.index;
  div.innerHTML = `<div>${checkable?`<input type="checkbox" class="rowcheck" value="${row.index}"/>`:""}</div><div class="thumbs">${frames}</div><div class="meta"><div class="name">${esc(row.source_name)}</div><div class="cell-status">${pill(row.review_status)}</div><div class="result"><div><b>Description</b><br><span class="cell-desc">${esc(row.description)}</span></div><div><b>Client / location</b><br><span class="cell-client">${esc(row.client_or_location)}</span></div><div><b>Proposed name</b><br><span class="cell-proposed">${esc(row.proposed_name)}</span></div></div><div class="cell-note muted">${esc(row.ai_rationale || '')}</div></div>`;
  host.appendChild(div);
}}
async function reviewOne(index){
  const div=document.querySelector(`.row[data-index="${index}"]`); if(div) div.querySelector('.cell-status').innerHTML=pill('working');
  const res = await fetch('/api/ai/review', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({indices:[index], provider:$("provider").value, model:$("model").value, api_key:$("apikey").value, preset:$("preset").value})}).then(r=>r.json());
  if(res.error){ if(div){ div.querySelector('.cell-status').innerHTML=pill('blocked'); div.querySelector('.cell-note').innerHTML=`<span class="err">${esc(res.error)}</span>`; } return {ok:false,error:res.error}; }
  const r=(res.results||[])[0]||{}; if(div){ div.querySelector('.cell-status').innerHTML=pill(r.status||''); div.querySelector('.cell-desc').textContent=r.description||''; div.querySelector('.cell-client').textContent=r.client_or_location||''; div.querySelector('.cell-proposed').textContent=r.proposed_name||''; div.querySelector('.cell-note').textContent=r.error || r.response || ''; }
  return r;
}
async function runReview(indices){ if(!indices.length){statusMsg('No pending rows selected.', 'err'); return;} $("reviewSelected").disabled=true; $("reviewAll").disabled=true; let approved=0; for(let i=0;i<indices.length;i++){ statusMsg(`Reviewing ${i+1} / ${indices.length}…`, ''); const r=await reviewOne(indices[i]); if(r.ok) approved++; } statusMsg(`Done — ${approved}/${indices.length} ready to rename. Review the rest manually.`, approved===indices.length?'ok':'warn'); await loadStatus(); }
$("reviewSelected").onclick=()=>runReview(selectedIndices()); $("reviewAll").onclick=()=>runReview(Array.from(pending)); $("refresh").onclick=loadStatus; $("preset").onchange=updateEstimate; $("provider").onchange=loadStatus; loadStatus();
</script></body></html>"""

# Backwards-compatible name for tests/imports that referenced the old page.
_GEMINI_PAGE_HTML = _AI_PAGE_HTML


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
                "frames": [Path(frame).name for frame in frame_paths],
            })
        return JSONResponse({
            "rows": rows_data,
            "threshold": 0.78,
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

    # ── /api/ai/status ──────────────────────────────────────────────────────
    @app.get("/api/ai/status")
    async def ai_status(provider: str = Query("gemini")) -> JSONResponse:
        from video_reviewer.ai_review import available_providers, pending_indices, provider_status

        status = provider_status(provider)
        return JSONResponse({
            "provider": status.provider_id,
            "display_name": status.display_name,
            "available": status.available,
            "needs_key": not status.has_key,
            "message": status.message,
            "default_model": status.default_model,
            "providers": available_providers(),
            "pending": pending_indices(manifest_path),
        })

    @app.get("/api/gemini/status")
    async def gemini_status() -> JSONResponse:
        return await ai_status("gemini")

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

        indices = body.get("indices")
        provider = (body.get("provider") or "gemini").strip()
        model = (body.get("model") or "").strip() or None
        api_key = (body.get("api_key") or "").strip() or None
        policy = ReviewPolicy.from_preset(body.get("preset") or "balanced")
        if not indices:
            indices = pending_indices(manifest_path)
        indices = [int(i) for i in indices]

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
            return JSONResponse({"error": f"AI review failed: {exc}"}, status_code=500)

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

    # ── /ai-review : self-contained review page ─────────────────────────────
    @app.get("/ai-review", response_class=HTMLResponse)
    async def ai_page() -> HTMLResponse:
        return HTMLResponse(_AI_PAGE_HTML)

    @app.get("/gemini", response_class=HTMLResponse)
    async def gemini_page() -> HTMLResponse:
        return HTMLResponse(_AI_PAGE_HTML)

    # ── SPA: serve static build ─────────────────────────────────────────────
    if _STATIC_DIR.exists() and any(_STATIC_DIR.iterdir()):
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")

    return app


def launch_gui(manifest_path: Path, host: str, port: int) -> None:
    import webbrowser
    import uvicorn

    webbrowser.open(f"http://{host}:{port}")
    uvicorn.run(create_app(manifest_path), host=host, port=port)
