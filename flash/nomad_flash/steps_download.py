"""Pipeline step: download ISO + prebuilt docker tarball from GitHub releases.

Replaces the old "user provides their own ISO" model. The flash tool now
fetches everything from a GitHub release by default, caching downloads
in ~/.cache/nomad-flash/<version>/ so re-flashing is fast.

Three classes of files come from a release:

  - live-image-amd64.hybrid.iso              the bootable ISO
  - nomad-docker-full.tar.xz.part-aa, ...    split prebuilt docker tree
                                              (parts get reassembled before
                                              extraction)

For "base only" mode, we still download the ISO but skip the docker tar.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from .runner import StepLogger

if TYPE_CHECKING:
    from .flash_pipeline import FlashConfig


# Where downloaded files live. Subdirectory per version so cache can hold
# multiple versions side-by-side. We use ~/.cache/ rather than /tmp to
# survive reboots — these are big files (5GB+) and re-downloading after
# a flash machine reboot would be painful.
CACHE_ROOT = Path.home() / ".cache" / "nomad-flash"

# Asset filename conventions.
ISO_NAME = "live-image-amd64.hybrid.iso"
DOCKER_TAR_PREFIX = "nomad-docker-full.tar.xz.part-"

GITHUB_API_BASE = "https://api.github.com"


# ---------- GitHub release lookup ----------

def _api_get(url: str) -> dict | list:
    """Fetch a JSON document from GitHub's API."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "nomad-flash",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_releases(owner: str, repo: str, max_count: int = 10) -> list[dict]:
    """List the most recent releases of a repo, newest first."""
    raw = _api_get(f"{GITHUB_API_BASE}/repos/{owner}/{repo}/releases")
    if not isinstance(raw, list):
        raise RuntimeError(f"unexpected releases payload from GitHub")
    out = []
    for r in raw[:max_count]:
        if r.get("draft") or r.get("prerelease"):
            continue
        out.append({
            "tag": r["tag_name"],
            "name": r.get("name") or r["tag_name"],
            "published_at": r.get("published_at"),
        })
    return out


def get_release(owner: str, repo: str, tag: str | None) -> dict:
    """Fetch a specific release (or the latest if tag is None)."""
    if tag is None or tag == "latest":
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/releases/latest"
    else:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/releases/tags/{tag}"
    return _api_get(url)


def _assets_by_name(release: dict) -> dict[str, dict]:
    return {a["name"]: a for a in release.get("assets", [])}


# ---------- HTTP download with progress ----------

def _emit_progress(log: StepLogger, current: int, total: int) -> None:
    """Emit a ::progress::N marker the frontend uses to drive the
    in-step progress bar (the part of the overall bar that's filling
    while *this* step is running).

    N is 0-100 — the percentage of the current step. The total flash
    progress is computed by the frontend as
        (completed_steps + current_step_fraction) / total_steps.

    We rate-limit ourselves to whole-percent updates to avoid spamming
    the WebSocket with millions of marker frames during a multi-GB
    download.
    """
    if total <= 0:
        return
    pct = (current * 100) // total
    log(f"::progress::{pct}")


def _download_to(url: str, dest: Path, log: StepLogger,
                 expected_size: int | None = None,
                 step_total_bytes: int = 0,
                 step_done_bytes: int = 0) -> int:
    """Stream a URL to a file, logging progress periodically.

    Returns the number of bytes written so callers can track cumulative
    progress across multiple downloads in one step.

    When step_total_bytes is provided, we emit ::progress:: markers
    showing this download's contribution to the overall step. So if
    we're downloading 3 parts that total 4GB, downloading the first
    part 50% of the way through emits progress at (1GB / 4GB) = 25%.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()

    log(f"  downloading {dest.name}")

    req = urllib.request.Request(url, headers={"User-Agent": "nomad-flash"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length", 0)) or expected_size or 0

        chunk_size = 1024 * 1024  # 1 MiB
        written = 0
        last_text_log_pct = -1
        last_progress_marker_pct = -1

        with tmp.open("wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)

                # Step-level progress marker (rate-limited to whole percent).
                # This drives the UI bar smoothly — without rate-limiting we'd
                # send thousands of markers per second on fast connections.
                if step_total_bytes > 0:
                    overall = step_done_bytes + written
                    overall_pct = (overall * 100) // step_total_bytes
                    if overall_pct != last_progress_marker_pct:
                        _emit_progress(log, overall, step_total_bytes)
                        last_progress_marker_pct = overall_pct

                # Human-readable text log at every 10% — useful in the
                # detailed-logs panel but not too noisy.
                if total > 0:
                    pct = (written * 100) // total
                    if pct >= last_text_log_pct + 10:
                        mb = written / (1024 * 1024)
                        total_mb = total / (1024 * 1024)
                        log(f"    {pct}%  ({mb:.0f} / {total_mb:.0f} MiB)")
                        last_text_log_pct = pct

    tmp.replace(dest)
    final_mb = dest.stat().st_size / (1024 * 1024)
    log(f"  done: {dest.name} ({final_mb:.0f} MiB)")
    return written


# ---------- file existence / cache management ----------

def _is_cached(path: Path, expected_size: int | None) -> bool:
    """Return True if `path` exists and looks complete."""
    if not path.is_file():
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.exists():
        return False
    if expected_size is not None and path.stat().st_size != expected_size:
        return False
    return True


def cache_dir_for_version(tag: str) -> Path:
    """Where files for a given release tag live."""
    return CACHE_ROOT / tag


def clear_cache_for_version(tag: str, log: StepLogger) -> None:
    """Wipe cached files for a single version."""
    d = cache_dir_for_version(tag)
    if d.exists():
        log(f"clearing cache: {d}")
        shutil.rmtree(d)
    else:
        log(f"no cache to clear at {d}")


def clear_all_cache(log: StepLogger) -> None:
    """Wipe everything."""
    if CACHE_ROOT.exists():
        log(f"clearing all flash-tool cache: {CACHE_ROOT}")
        shutil.rmtree(CACHE_ROOT)


# ---------- the pipeline step ----------

def step_download_release(cfg: "FlashConfig", log: StepLogger) -> None:
    """Download the ISO (and prebuilt tar parts, if not in base-only mode).

    Sets cfg.iso_path and cfg.prebuilt_docker_path on completion so
    later pipeline steps find what they need.
    """
    needs_iso = not (cfg.iso_path and Path(cfg.iso_path).is_file())
    needs_docker = (
        cfg.flash_mode == "full"
        and not (cfg.prebuilt_docker_path and Path(cfg.prebuilt_docker_path).is_file())
    )

    if not needs_iso and not needs_docker:
        log("all required files already present locally, skipping download")
        # Mark step complete so the UI bar lands at 100%.
        log("::progress::100")
        return

    log(f"resolving release: {cfg.github_owner}/{cfg.github_repo} "
        f"@ {cfg.version or 'latest'}")
    release = get_release(cfg.github_owner, cfg.github_repo, cfg.version)
    tag = release["tag_name"]
    log(f"using release {tag} (published {release.get('published_at', '?')})")

    assets = _assets_by_name(release)
    cache = cache_dir_for_version(tag)
    cache.mkdir(parents=True, exist_ok=True)

    # Pre-compute total bytes we need to fetch so the progress bar
    # represents the whole step, not just the current file.
    iso_asset = None
    docker_assets: list[dict] = []
    step_total = 0

    if needs_iso:
        if ISO_NAME not in assets:
            raise RuntimeError(
                f"release {tag} has no asset named {ISO_NAME!r}; "
                f"available: {sorted(assets.keys())}"
            )
        iso_asset = assets[ISO_NAME]
        # Only count toward total if we'll actually download (not cached).
        iso_dest = cache / ISO_NAME
        if not _is_cached(iso_dest, iso_asset.get("size")):
            step_total += iso_asset.get("size", 0)

    if needs_docker:
        part_names = sorted(n for n in assets if n.startswith(DOCKER_TAR_PREFIX))
        if not part_names:
            raise RuntimeError(
                f"release {tag} has no docker tar parts "
                f"(expected names starting with {DOCKER_TAR_PREFIX!r})"
            )
        for name in part_names:
            a = assets[name]
            docker_assets.append(a)
            part_dest = cache / name
            if not _is_cached(part_dest, a.get("size")):
                step_total += a.get("size", 0)

    step_done = 0  # cumulative bytes actually downloaded so far in this step

    # ---- ISO ----
    if needs_iso and iso_asset is not None:
        iso_dest = cache / ISO_NAME
        if _is_cached(iso_dest, iso_asset.get("size")):
            log(f"ISO cached: {iso_dest}")
        else:
            written = _download_to(
                iso_asset["browser_download_url"], iso_dest, log,
                expected_size=iso_asset.get("size"),
                step_total_bytes=step_total,
                step_done_bytes=step_done,
            )
            step_done += written
        cfg.iso_path = str(iso_dest)

    # ---- prebuilt docker tar parts ----
    if needs_docker:
        local_parts: list[Path] = []
        for a in docker_assets:
            name = a["name"]
            part_dest = cache / name
            if _is_cached(part_dest, a.get("size")):
                log(f"part cached: {name}")
            else:
                written = _download_to(
                    a["browser_download_url"], part_dest, log,
                    expected_size=a.get("size"),
                    step_total_bytes=step_total,
                    step_done_bytes=step_done,
                )
                step_done += written
            local_parts.append(part_dest)

        # Reassemble.
        combined = cache / "nomad-docker-full.tar.xz"
        expected_total = sum(p.stat().st_size for p in local_parts)
        if combined.is_file() and combined.stat().st_size == expected_total:
            log(f"reassembled tar already in cache: {combined}")
        else:
            log(f"reassembling {len(local_parts)} parts -> {combined.name}")
            tmp = combined.with_suffix(combined.suffix + ".tmp")
            if tmp.exists():
                tmp.unlink()
            with tmp.open("wb") as out:
                for part in local_parts:
                    with part.open("rb") as p:
                        shutil.copyfileobj(p, out, length=4 * 1024 * 1024)
            tmp.replace(combined)
            log(f"  reassembled: {combined.stat().st_size // (1024*1024)} MiB")

        cfg.prebuilt_docker_path = str(combined)

    # Step is fully done.
    log("::progress::100")
