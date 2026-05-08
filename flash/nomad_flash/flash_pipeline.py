"""Flash pipeline orchestration.

The pipeline is broken into discrete steps. Each step:
  - Has a name (for the UI's progress display)
  - Yields log lines as it runs (streamed to the WebSocket)
  - Either finishes successfully or raises an exception

The orchestrator runs steps in order, wraps each in start/end markers
that the frontend can use to highlight progress, and turns exceptions
into clean error messages instead of stack traces.

Two flash modes are supported in v1:

  FULL MODE (default, recommended):
    Download the ISO + prebuilt docker tarball from a GitHub release.
    Partition USB, copy ISO, extract docker tree onto NOMAD_DATA.
    First boot is fast (~2-3 min). Works fully offline once flashed.

  BASE ONLY MODE:
    Download the ISO. Skip the docker tarball entirely. First boot
    will need internet access — `docker compose up` pulls images
    fresh from the registry. For users who want the very latest
    images and have internet on first boot.

build_pipeline() picks the right steps for the mode.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

# Default GitHub repo we pull releases from. The CLI / web UI can
# override this if someone forks the project, but most users won't.
DEFAULT_GITHUB_OWNER = "joz1trillion"
DEFAULT_GITHUB_REPO = "nomad-usb"


@dataclass
class FlashConfig:
    """Everything the pipeline needs to know to do its job."""

    # ---- target ----
    device: str           # e.g. "/dev/sdb"

    # ---- mode ----
    # "full" or "base". Full means download+extract prebuilt docker tar.
    # Base means skip the docker tar — first boot pulls images from the
    # internet via docker compose.
    flash_mode: str = "full"

    # ---- release source ----
    github_owner: str = DEFAULT_GITHUB_OWNER
    github_repo: str = DEFAULT_GITHUB_REPO
    # None or "latest" means latest release; otherwise a tag like "v0.6.0".
    version: str | None = None

    # ---- local file overrides (advanced) ----
    # If set, skip downloading the ISO and use this path instead.
    iso_path: str | None = None
    # If set, skip downloading the docker tar and use this path instead.
    # Only meaningful in "full" mode.
    prebuilt_docker_path: str | None = None

    # ---- legacy / power-user knobs ----
    # No longer used in v1's two-mode UX, but kept on the config so older
    # CLI invocations don't break.
    apps: list[str] = field(default_factory=list)
    archive_path: str | None = None
    no_cache: bool = False


# The signature for a step: takes config + a logger callable, yields nothing.
# Steps log via the callable rather than returning lines so they can stream
# in real time.
StepLogger = Callable[[str], None]
StepFunc = Callable[["FlashConfig", StepLogger], None]


@dataclass
class Step:
    name: str             # short title for the UI
    fn: StepFunc          # the function that does the work


# Steps are imported lazily so a missing optional dep in one step doesn't
# break the whole pipeline import.
from .steps_partition import step_partition            # noqa: E402
from .steps_iso import step_copy_iso                   # noqa: E402
from .steps_images import step_write_compose           # noqa: E402
from .steps_prebuilt import step_deploy_prebuilt_docker  # noqa: E402
from .steps_download import step_download_release      # noqa: E402


def build_pipeline(cfg: FlashConfig) -> list[Step]:
    """Construct the right pipeline for this flash.

    The shape is always:
        [Download] → Partition → Copy ISO → [Deploy docker?] → Write compose

    Download runs first because every other step needs files it
    produces (ISO at minimum, docker tarball if mode=full). It's
    skipped if both files are already provided locally via the
    advanced overrides — in that case we have nothing to fetch.

    Deploy docker only runs in full mode. In base mode, the USB
    boots with no images and pulls them from the internet on first
    boot via docker compose.
    """
    steps: list[Step] = [
        Step("Download release", step_download_release),
        Step("Partition",        step_partition),
        Step("Copy ISO",         step_copy_iso),
    ]

    if cfg.flash_mode == "full":
        steps.append(Step("Deploy docker", step_deploy_prebuilt_docker))
    elif cfg.flash_mode == "base":
        # No-op for docker — base mode skips it entirely.
        pass
    else:
        raise ValueError(
            f"unknown flash_mode {cfg.flash_mode!r}; expected 'full' or 'base'"
        )

    steps.append(Step("Write compose", step_write_compose))
    return steps


def run_pipeline(cfg: FlashConfig, log: StepLogger,
                 pipeline: list[Step] | None = None) -> None:
    """Run all steps in order. Streams via `log`. Raises on first failure."""
    if pipeline is None:
        pipeline = build_pipeline(cfg)

    log("=== Flash starting ===")
    log(f"Target: {cfg.device}")
    log(f"Mode:   {cfg.flash_mode}")
    log(f"Source: {cfg.github_owner}/{cfg.github_repo} @ {cfg.version or 'latest'}")
    if cfg.iso_path:
        log(f"ISO override: {cfg.iso_path}")
    if cfg.prebuilt_docker_path:
        log(f"Docker tar override: {cfg.prebuilt_docker_path}")
    log("")

    for i, step in enumerate(pipeline, 1):
        # The "::step::" prefix is a sentinel the frontend uses to
        # advance its progress indicator. Anything else is a normal log line.
        log(f"::step::{i}/{len(pipeline)}::{step.name}")
        try:
            step.fn(cfg, log)
        except Exception as e:
            log(f"::error::Step '{step.name}' failed: {e}")
            raise
        log("")

    log("::done::")
    log("=== Flash complete ===")
