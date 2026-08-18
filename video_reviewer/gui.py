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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from video_reviewer.config import save_correction
from video_reviewer.manifest import (
    manifest_transaction,
    REVIEW_APPROVED,
    read_manifest_csv,
    write_manifest_csv,
)
from video_reviewer.sop import build_proposed_name

logger = logging.getLogger("video_renamer")

_STATIC_DIR = Path(__file__).parent / "static"


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
  .row { display:grid; grid-template-columns:auto 1fr; gap:12px; border:1px solid #8883; border-radius:14px; padding:12px; align-items:start; }
  .rowbody { display:grid; gap:8px; min-width:0; }
  .rowhead { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
  .thumbs { display:flex; flex-wrap:wrap; gap:6px; }
  .thumbs img { width:72px; height:48px; object-fit:cover; border-radius:8px; border:1px solid #8884; }
  .meta { display:grid; gap:4px; }
  .name { font-weight:700; word-break:break-word; }
  .result { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:8px; font-size:.88rem; }
  .folder-browser { margin-top:12px; padding:12px; border:1px solid #8884; border-radius:12px; }
  .folder-list { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:7px; margin:10px 0; max-height:260px; overflow:auto; }
  .folder-list button { text-align:left; background:#374151; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  details { margin-top:8px; } summary { cursor:pointer; }
</style>
</head>
<body><main>
  <h1>AI Review</h1>
  <p class="sub">Guided review for nontechnical users: prepare videos, send representative frames to an API provider, confirm uncertain rows, preview, then rename.</p>
  <div class="steps"><span class="step active">1 AI setup</span><span class="step">2 choose videos</span><span class="step">3 review uncertain</span><span class="step">4 preview/apply in main app</span></div>

  <div class="cards">
    <section class="card"><h2>Provider</h2><p class="muted">Choose a vision provider. Keys are used for the request only and never written to the manifest.</p><div id="providers"></div></section>
    <section class="card privacy"><h2>Privacy + cost</h2><p>External API review sends only sampled frames and metadata for selected rows. API keys are used for the request only and are not written to the manifest.</p><p id="estimate" class="muted">Choose a preset to see the frame budget.</p></section>
    <section class="card"><h2>Recommended defaults</h2><p class="muted">Use <b>Balanced</b> first. It keeps full-resolution sampled frames, limits frames per video, and sends uncertain rows to manual review instead of guessing.</p></section>
  </div>

  <div id="status">Checking AI provider…</div>

  <section class="card">
    <h2>Choose videos</h2>
    <p class="muted">No terminal path arguments needed. Pick a folder, then prepare will create the manifest and sample frames for this session.</p>
    <div class="bar">
      <label class="field">Video folder
        <input id="inputDir" placeholder="/path/to/videos" />
      </label>
      <label class="field">Year-month override
        <input id="yearMonth" placeholder="optional, e.g. 2024-07" />
      </label>
      <button id="pickFolder" class="secondary">Choose folder</button>
      <button id="browseFolder" class="secondary">Browse in app</button>
      <button id="prepareVideos">Prepare videos</button>
    </div>
    <p id="prepareStatus" class="muted">After preparation, rows will appear below.</p>
    <div id="folderBrowser" class="folder-browser" hidden>
      <div class="bar">
        <label class="field">Current folder
          <input id="browserPath" placeholder="Folder path" />
        </label>
        <button id="openBrowserPath" class="secondary">Open path</button>
        <button id="browserUp" class="secondary">Up</button>
        <button id="useBrowserFolder">Use this folder</button>
        <button id="closeBrowser" class="secondary">Cancel</button>
      </div>
      <div id="folderList" class="folder-list"></div>
      <p id="browserStatus" class="muted"></p>
    </div>
  </section>

  <div class="bar">
    <label class="field">Provider
      <select id="provider"></select>
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
let pending = new Set(); let allRows = []; let providerNeedsKey = true; let providerAvailable = false;
function esc(s){ return String(s||"").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function pill(status){ const cls=["pending","approved","needs_review","blocked","missing","working"].includes(status)?status:"muted"; return `<span class="pill ${cls}">${esc(status)}</span>`; }
function statusMsg(text, cls){ const el=$("status"); el.textContent=text; el.className=cls||""; }
function selectedIndices(){ return Array.from(document.querySelectorAll(".rowcheck:checked")).map(c=>Number(c.value)); }
function presetFrames(){ return {fast:6, balanced:12, accurate:16}[$("preset").value] || 12; }
function updateEstimate(){ const n = pending.size; const f = presetFrames(); const total=n*f; const tier= total<=60?'low':total<=250?'medium':'high'; $("estimate").textContent = `${n} pending video(s) × up to ${f} frames = up to ${total} images (${tier} cost tier).`; }
function updateReviewAvailability(){
  const hasKey = !providerNeedsKey || $("apikey").value.trim().length > 0;
  const enabled = providerAvailable && pending.size > 0 && hasKey;
  $("reviewSelected").disabled = !enabled;
  $("reviewAll").disabled = !enabled;
}
function prepMsg(text, cls){ const el=$("prepareStatus"); el.textContent=text; el.className=cls||"muted"; }
async function pickFolder(){
  prepMsg('Opening folder picker…', 'muted');
  try { const response = await fetch('/api/pick-dir'); const res = await response.json();
    if(res.path){ $("inputDir").value = res.path; prepMsg('Folder selected. Click Prepare videos.', 'ok'); }
    else if(res.cancelled) prepMsg('Folder picker cancelled. You can use Browse in app instead.', 'warn');
    else { prepMsg(res.error || 'Native folder picker unavailable. Use Browse in app instead.', 'err'); await showFolderBrowser(); }
  } catch (error) { prepMsg('Native folder picker failed. Use Browse in app instead.', 'err'); await showFolderBrowser(); }
}
async function browseTo(path){
  const status = $("browserStatus"); status.textContent = 'Loading folders…';
  try {
    const response = await fetch(`/api/browse-dir?path=${encodeURIComponent(path || '')}`);
    const data = await response.json();
    if(!response.ok){ status.textContent = data.error || 'Could not open this folder.'; return; }
    $("browserPath").value = data.path;
    $("browserUp").dataset.path = data.parent;
    const list = $("folderList"); list.innerHTML = '';
    for(const entry of data.dirs || []){
      const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary';
      button.textContent = `📁 ${entry.name}`; button.title = entry.path; button.onclick = ()=>browseTo(entry.path);
      list.appendChild(button);
    }
    status.textContent = data.dirs?.length ? 'Choose a folder or use the current folder.' : 'No subfolders here. You can use the current folder.';
  } catch (error) { status.textContent = 'Could not load folders from the local app.'; }
}
async function showFolderBrowser(){
  $("folderBrowser").hidden = false;
  await browseTo($("inputDir").value.trim());
}
function useBrowserFolder(){
  $("inputDir").value = $("browserPath").value;
  $("folderBrowser").hidden = true;
  prepMsg('Folder selected. Click Prepare videos.', 'ok');
}
async function prepareVideos(){
  const input = $("inputDir").value.trim();
  if(!input){ prepMsg('Choose or paste a video folder first.', 'err'); return; }
  const base = input.endsWith('/') ? input.slice(0, -1) : input;
  prepMsg('Preparing videos… this can take a few minutes for large files.', 'warn');
  $("prepareVideos").disabled = true;
  try {
    const response = await fetch('/api/run-prepare', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({input_dir:input, year_month:$("yearMonth").value.trim(), tmp_dir:`${base}/.video-renamer-tmp`, sample_count:0, proxy_scale:1280})});
    const res = await response.json();
    if(res.error){ prepMsg(res.error, 'err'); return; }
    prepMsg(res.message || 'Prepared videos.', 'ok');
    await loadStatus();
  } catch (error) {
    prepMsg('Preparation failed or the server became unavailable. Your existing batch was left unchanged.', 'err');
  } finally {
    $("prepareVideos").disabled = false;
  }
}
async function loadStatus(){
  const provider=$("provider").value;
  const [statusRes, rowsRes] = await Promise.all([
    fetch(`/api/ai/status?provider=${encodeURIComponent(provider)}`).then(r=>r.json()),
    fetch("/api/rows").then(r=>r.json()),
  ]);
  pending = new Set(statusRes.pending || []); allRows = rowsRes.rows || [];
  providerAvailable = Boolean(statusRes.available);
  providerNeedsKey = Boolean(statusRes.needs_key);
  const providerOptions = statusRes.providers || [];
  const select = $("provider"); const current = provider;
  select.innerHTML = providerOptions.map(p=>`<option value="${esc(p.id)}">${esc(p.display_name)}</option>`).join("");
  if(providerOptions.some(p=>p.id===current)) select.value = current;
  $("providers").innerHTML = providerOptions.map(p=>`<div>${esc(p.display_name)} <span class="muted">default ${esc(p.default_model)} · key ${esc((p.env_key_names||[]).join(' or '))}</span></div>`).join("");
  $("apikey").placeholder = statusRes.env_key_names?.length ? `paste key, or set ${statusRes.env_key_names.join(' / ')}` : 'paste provider API key';
  if(!statusRes.available) statusMsg(statusRes.message, "err");
  else if(statusRes.needs_key) statusMsg(statusRes.message, "warn");
  else statusMsg(`${statusRes.display_name || 'AI provider'} ready — ${pending.size} video(s) pending.`, "ok");
  updateEstimate(); updateReviewAvailability(); renderRows(allRows);
}
function renderRows(rows){ const host=$("rows"); host.innerHTML=""; for(const row of rows){
  const checkable = pending.has(row.index);
  const frames = (row.frames || []).slice(0,4).map((_,i)=>`<img alt="frame ${i+1}" src="/api/frame/${row.index}/${i}" loading="lazy"/>`).join("");
  const div=document.createElement("div"); div.className="row"; div.dataset.index=row.index;
  div.innerHTML = `<div>${checkable?`<input type="checkbox" class="rowcheck" value="${row.index}"/>`:""}</div><div class="rowbody"><div class="rowhead"><span class="name">${esc(row.source_name)}</span><span class="cell-status">${pill(row.review_status)}</span></div><div class="thumbs">${frames}</div><div class="meta"><div class="result"><div><b>Description</b><br><span class="cell-desc">${esc(row.description)}</span></div><div><b>Client / location</b><br><span class="cell-client">${esc(row.client_or_location)}</span></div><div><b>Proposed name</b><br><span class="cell-proposed">${esc(row.proposed_name)}</span></div></div><div class="cell-note muted">${esc(row.ai_rationale || '')}</div></div></div>`;
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
function changeProvider(){
  $("apikey").value = '';
  loadStatus();
}
$("pickFolder").onclick=pickFolder; $("browseFolder").onclick=showFolderBrowser; $("openBrowserPath").onclick=()=>browseTo($("browserPath").value); $("browserUp").onclick=()=>browseTo($("browserUp").dataset.path); $("useBrowserFolder").onclick=useBrowserFolder; $("closeBrowser").onclick=()=>{$("folderBrowser").hidden=true;}; $("prepareVideos").onclick=prepareVideos; $("reviewSelected").onclick=()=>runReview(selectedIndices()); $("reviewAll").onclick=()=>runReview(Array.from(pending)); $("refresh").onclick=loadStatus; $("preset").onchange=updateEstimate; $("provider").onchange=changeProvider; $("apikey").oninput=updateReviewAvailability; loadStatus();
</script></body></html>"""

# Backwards-compatible name for tests/imports that referenced the old page.
_GEMINI_PAGE_HTML = _AI_PAGE_HTML


def create_app(manifest_path: Path) -> FastAPI:
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
                new_client = edit.get("client_or_location", row.client_or_location)
                if row.description and (new_desc != row.description or new_client != row.client_or_location):
                    corrections.append((Path(row.source_path).name, row.description, row.client_or_location, new_desc, new_client))
                row.description = new_desc
                row.client_or_location = new_client
                row.year_month = edit.get("year_month", row.year_month)
                row.review_status = REVIEW_APPROVED
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

        status = provider_status(provider)
        return JSONResponse({
            "provider": status.provider_id,
            "display_name": status.display_name,
            "available": status.available,
            "needs_key": not status.has_key,
            "message": status.message,
            "default_model": status.default_model,
            "env_key_names": list(status.env_key_names),
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

        indices = body.get("indices") if "indices" in body else None
        provider = (body.get("provider") or "gemini").strip()
        model = (body.get("model") or "").strip() or None
        api_key = (body.get("api_key") or "").strip() or None
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

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "Video Renamer has no remote authentication and can only bind to localhost. "
            "Use --host 127.0.0.1."
        )
    selected_port = _choose_port(host, port)
    if selected_port != port:
        print(f"Port {port} is already in use; starting Video Renamer on port {selected_port} instead.")
    url = f"http://{host}:{selected_port}/ai-review"
    print(f"Opening Video Renamer: {url}")
    webbrowser.open(url)
    uvicorn.run(create_app(manifest_path), host=host, port=selected_port)


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
