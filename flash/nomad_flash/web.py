"""Web UI server.

Serves the wizard SPA on / and exposes a small JSON API plus a
WebSocket for real-time progress updates during the flash itself.

For now this is a skeleton — the real flashing logic gets wired in
in subsequent steps. Right now it just serves the static frontend
and a /api/devices endpoint for proving the plumbing works.
"""

from __future__ import annotations

import asyncio
import json
import threading
import webbrowser
from pathlib import Path
from queue import Queue, Empty

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

from .flash_pipeline import FlashConfig, run_pipeline

# Resolve our package's data directories at import time so we can
# fail fast if the install is broken (missing static/, etc.).
_PKG_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _PKG_DIR / "static"
_TEMPLATES_DIR = _PKG_DIR / "templates"

app = FastAPI(title="nomad-flash", version="0.1.0")

# Mount the static dir under /static. The single-page app loads its
# JS and CSS from here; the entry HTML page is server-rendered (below)
# so we can inject runtime config.
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

_jinja = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=True,
)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Render the wizard entry page."""
    template = _jinja.get_template("index.html")
    return HTMLResponse(template.render(title="Nomad Flash"))


@app.get("/api/health")
async def health() -> dict:
    """Simple liveness check — useful for the frontend to confirm
    the backend is reachable before kicking off any real work.

    Also reports the package version (sourced from the repo's VERSION
    file at runtime) and whether we're running as root, so the
    frontend can warn the user up front rather than failing partway
    through a flash.
    """
    import os
    from . import __version__
    return {
        "ok": True,
        "version": __version__,
        "is_root": os.geteuid() == 0,
    }


@app.get("/api/devices")
async def devices() -> dict:
    """List available block devices for flashing.

    Filters out read-only devices, non-disks, and the live boot
    device. Removable drives are listed first. See devices.py for
    the full filtering/sorting logic.
    """
    from .devices import list_devices_dict
    try:
        return {"devices": list_devices_dict()}
    except RuntimeError as e:
        # Surface as an HTTP error rather than a 500. The frontend's
        # generic error handler will display the message inline.
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e)) from e


# ---------- file picker (zenity + in-app browser) ----------

@app.get("/api/picker/zenity-available")
async def zenity_check() -> dict:
    """Tell the frontend whether to show a "Browse…" button.

    True if zenity is installed AND we have a desktop session — both
    are required for the popup to actually work.
    """
    from .browse import zenity_available
    return {"available": zenity_available()}


@app.post("/api/picker/zenity")
async def zenity_pick() -> dict:
    """Pop a native file picker, return the chosen path.

    Returns {"path": "..."} on selection, {"path": null} on cancel
    or any failure. The frontend treats null as "user cancelled,
    nothing to do" rather than as an error.
    """
    from .browse import pick_iso_with_zenity
    return {"path": pick_iso_with_zenity()}


@app.get("/api/picker/browse")
async def browse(path: str | None = None) -> dict:
    """List a directory for the in-app file browser.

    Defaults to ~/Downloads. Returns directories + .iso files; other
    files are visible but flagged as not selectable.
    """
    from .browse import list_directory_dict
    return list_directory_dict(path)


@app.get("/api/apps")
async def apps() -> dict:
    """Return the catalog of optional Nomad apps the user can bundle."""
    from .apps_catalog import OPTIONAL_APPS
    return {
        "apps": [
            {
                "key": a.key,
                "name": a.name,
                "description": a.description,
                "images": a.images,
                "approx_mb": a.approx_mb,
            }
            for a in OPTIONAL_APPS
        ],
    }


@app.websocket("/ws/flash")
async def ws_flash(websocket: WebSocket) -> None:
    """Drive the flash pipeline over a WebSocket.

    Protocol:
      Client connects, sends a single JSON message with the FlashConfig
      fields. Server runs the pipeline in a worker thread, streaming
      log lines back as text messages. When the pipeline finishes (or
      errors), the server closes the socket.

    Why a thread? The pipeline is heavily I/O-bound but synchronous
    (subprocess calls, blocking docker SDK calls). Running it in a
    thread keeps it from blocking the event loop, and lets us pump
    its output through a thread-safe Queue to the async WebSocket.
    """
    await websocket.accept()

    try:
        # First message from the client should be the config JSON.
        raw = await websocket.receive_text()
        cfg_dict = json.loads(raw)
        cfg = FlashConfig(
            iso_path=cfg_dict["iso_path"],
            device=cfg_dict["device"],
            apps=cfg_dict.get("apps", []),
            archive_path=cfg_dict.get("archive_path"),
            no_cache=cfg_dict.get("no_cache", False),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        await websocket.send_text(f"::error::Invalid config: {e}")
        await websocket.close()
        return

    # Thread-safe queue between the worker (sync) and the WebSocket pump (async).
    log_queue: Queue[str | None] = Queue()  # sentinel None means "done"

    def worker_log(line: str) -> None:
        log_queue.put(line)

    def worker() -> None:
        try:
            run_pipeline(cfg, worker_log)
        except Exception:
            # The pipeline already logged the error message via worker_log
            # before raising. We just need to terminate cleanly.
            pass
        finally:
            log_queue.put(None)  # signal end of stream

    threading.Thread(target=worker, daemon=True).start()

    # Pump the queue to the WebSocket. Use run_in_executor for the
    # blocking queue.get so we don't tie up the event loop.
    loop = asyncio.get_running_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, log_queue.get)
            if line is None:
                break
            await websocket.send_text(line)
    except WebSocketDisconnect:
        # Client closed early — worker keeps running in the background
        # but its log lines just go into a queue that nobody reads.
        # That's fine for v1 (we said no cancel mid-flash).
        return

    await websocket.close()


def run_server(host: str = "127.0.0.1", port: int = 5050,
               open_browser: bool = True) -> None:
    """Start the uvicorn server, optionally opening the browser."""
    import uvicorn

    if open_browser:
        # Defer the browser launch slightly so the server has a moment
        # to bind. A second is more than enough on any reasonable host.
        url = f"http://{host}:{port}/"
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        print(f"Opening browser to {url}")

    print(f"nomad-flash web UI listening on http://{host}:{port}/")
    print("Press Ctrl+C to stop.")
    uvicorn.run(app, host=host, port=port, log_level="info")
