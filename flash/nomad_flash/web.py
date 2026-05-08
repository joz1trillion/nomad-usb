"""Web UI server.

Serves the wizard SPA on / and exposes a small JSON API plus a
WebSocket for real-time progress updates during the flash itself.
"""

from __future__ import annotations

import asyncio
import json
import threading
import webbrowser
from pathlib import Path
from queue import Queue, Empty

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

from .flash_pipeline import (
    FlashConfig, run_pipeline,
    DEFAULT_GITHUB_OWNER, DEFAULT_GITHUB_REPO,
)


_PKG_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _PKG_DIR / "static"
_TEMPLATES_DIR = _PKG_DIR / "templates"


app = FastAPI(title="nomad-flash")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
_jinja = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=True,
)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    template = _jinja.get_template("index.html")
    return HTMLResponse(template.render(title="Nomad Flash"))


# ---------- meta endpoints ----------

@app.get("/api/health")
async def health() -> dict:
    """Liveness check + version + root status."""
    import os
    from . import __version__
    return {
        "ok": True,
        "version": __version__,
        "is_root": os.geteuid() == 0,
        "default_owner": DEFAULT_GITHUB_OWNER,
        "default_repo": DEFAULT_GITHUB_REPO,
    }


@app.get("/api/devices")
async def devices() -> dict:
    """List available block devices for flashing."""
    from .devices import list_devices_dict
    try:
        return {"devices": list_devices_dict()}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ---------- file picker (in-app browser) ----------

@app.get("/api/picker/browse")
async def browse(path: str | None = None,
                 exts: str | None = None) -> dict:
    """List a directory for the in-app file browser.

    Query params:
      path:  directory to list (default: ~/Downloads)
      exts:  comma-separated extensions to flag as selectable
             (e.g. ".iso" or ".gz,.xz,.tar")

    The frontend uses `is_match` per-entry to style selectable files.
    """
    from .browse import list_directory_dict

    allowed_exts: list[str] | None = None
    if exts:
        allowed_exts = [e.strip() for e in exts.split(",") if e.strip()]

    return list_directory_dict(path, allowed_exts=allowed_exts)


# ---------- release listing ----------

@app.get("/api/releases")
async def releases(owner: str = DEFAULT_GITHUB_OWNER,
                   repo: str = DEFAULT_GITHUB_REPO) -> dict:
    """List recent releases on the configured GitHub repo."""
    from .steps_download import list_releases
    try:
        return {
            "owner": owner,
            "repo": repo,
            "releases": list_releases(owner, repo),
            "error": None,
        }
    except Exception as e:
        return {
            "owner": owner,
            "repo": repo,
            "releases": [],
            "error": str(e),
        }


# ---------- cache management ----------

@app.get("/api/cache")
async def cache_status() -> dict:
    """Report what's in the local download cache."""
    from .steps_download import CACHE_ROOT

    if not CACHE_ROOT.exists():
        return {"root": str(CACHE_ROOT), "versions": [], "total_bytes": 0}

    versions = []
    grand_total = 0
    for version_dir in sorted(CACHE_ROOT.iterdir()):
        if not version_dir.is_dir():
            continue
        size = 0
        files = []
        for f in version_dir.iterdir():
            if f.is_file():
                fsz = f.stat().st_size
                size += fsz
                files.append({"name": f.name, "size": fsz})
        versions.append({
            "tag": version_dir.name,
            "bytes": size,
            "files": files,
        })
        grand_total += size

    return {
        "root": str(CACHE_ROOT),
        "versions": versions,
        "total_bytes": grand_total,
    }


@app.delete("/api/cache")
async def clear_cache(version: str | None = None) -> dict:
    """Wipe one version's cache (or everything)."""
    from .steps_download import clear_cache_for_version, clear_all_cache

    cleared: list[str] = []

    def _capture(msg: str) -> None:
        cleared.append(msg)

    if version:
        clear_cache_for_version(version, _capture)
    else:
        clear_all_cache(_capture)

    return {"cleared": cleared}


# ---------- the flash pipeline ----------

@app.websocket("/ws/flash")
async def ws_flash(websocket: WebSocket) -> None:
    """Drive the flash pipeline over a WebSocket."""
    await websocket.accept()

    try:
        raw = await websocket.receive_text()
        cfg_dict = json.loads(raw)

        cfg = FlashConfig(
            device=cfg_dict["device"],
            flash_mode=cfg_dict.get("flash_mode", "full"),
            version=cfg_dict.get("version") or None,
            github_owner=cfg_dict.get("github_owner") or DEFAULT_GITHUB_OWNER,
            github_repo=cfg_dict.get("github_repo") or DEFAULT_GITHUB_REPO,
            iso_path=cfg_dict.get("iso_path") or None,
            prebuilt_docker_path=cfg_dict.get("prebuilt_docker_path") or None,
        )
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        await websocket.send_text(f"::error::Invalid config: {e}")
        await websocket.close()
        return

    log_queue: Queue[str | None] = Queue()

    def worker_log(line: str) -> None:
        log_queue.put(line)

    def worker() -> None:
        try:
            run_pipeline(cfg, worker_log)
        except Exception:
            pass
        finally:
            log_queue.put(None)

    threading.Thread(target=worker, daemon=True).start()

    loop = asyncio.get_running_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, log_queue.get)
            if line is None:
                break
            await websocket.send_text(line)
    except WebSocketDisconnect:
        return

    await websocket.close()


def run_server(host: str = "127.0.0.1", port: int = 5050,
               open_browser: bool = True) -> None:
    """Start the uvicorn server, optionally opening the browser."""
    import uvicorn

    if open_browser:
        url = f"http://{host}:{port}/"
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        print(f"Opening browser to {url}")

    print(f"nomad-flash web UI listening on http://{host}:{port}/")
    print("Press Ctrl+C to stop.")
    uvicorn.run(app, host=host, port=port, log_level="info")
