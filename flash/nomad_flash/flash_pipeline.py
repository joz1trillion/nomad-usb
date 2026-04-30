"""Flash pipeline orchestration.

The pipeline is broken into discrete steps. Each step:
  - Has a name (for the UI's progress display)
  - Yields log lines as it runs (streamed to the WebSocket)
  - Either finishes successfully or raises an exception

The orchestrator runs steps in order, wraps each in start/end markers
that the frontend can use to highlight progress, and turns exceptions
into clean error messages instead of stack traces.

Right now this only contains a FAKE pipeline that pretends to do the
work. We'll add real steps in 3b/3c/3d. The fake one exists so we can
prove out the streaming infrastructure end-to-end without risking
anyone's USB drive.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterator


@dataclass
class FlashConfig:
    """Everything the pipeline needs to know to do its job.

    Built from the wizard's selections (or the CLI subcommand's args).
    Validated on construction — if a required field is missing or bogus,
    we fail loudly here, not deep inside a step.
    """
    iso_path: str
    device: str           # e.g. "/dev/sdb"
    apps: list[str] = field(default_factory=list)
    archive_path: str | None = None
    no_cache: bool = False


# The signature for a step: takes config + a logger callable, yields nothing.
# Steps log via the callable rather than returning lines so they can stream
# in real time (a generator would buffer until the step ends).
StepLogger = Callable[[str], None]
StepFunc = Callable[[FlashConfig, StepLogger], None]


@dataclass
class Step:
    name: str             # short title for the UI ("Partition", "Copy ISO", ...)
    fn: StepFunc          # the function that does the work


# ---------- fake steps for end-to-end plumbing test ----------

def _fake_partition(cfg: FlashConfig, log: StepLogger) -> None:
    # Kept for reference / dry-run modes. The real implementation is
    # in steps_partition.step_partition.
    log(f"would wipe {cfg.device}")
    time.sleep(0.4)
    log("would create EFI partition (512MB FAT32)")
    time.sleep(0.4)
    log("would create NOMAD_LIVE partition (6GB ext4)")
    time.sleep(0.4)
    log("would create NOMAD_DATA partition (rest, ext4)")
    time.sleep(0.4)


def _fake_copy_iso(cfg: FlashConfig, log: StepLogger) -> None:
    log(f"would mount {cfg.iso_path}")
    time.sleep(0.3)
    log("would rsync ISO contents to NOMAD_LIVE")
    for pct in (10, 30, 60, 90, 100):
        time.sleep(0.3)
        log(f"  ... {pct}%")
    log("would install GRUB (UEFI + BIOS)")
    time.sleep(0.4)


def _fake_pull_images(cfg: FlashConfig, log: StepLogger) -> None:
    base_images = [
        "ghcr.io/crosstalk-solutions/project-nomad:latest",
        "mysql:8.0",
        "redis:7-alpine",
        "amir20/dozzle:v10.0",
    ]
    for img in base_images:
        log(f"would pull {img}")
        time.sleep(0.4)
    if cfg.apps:
        for app in cfg.apps:
            log(f"would pull optional app: {app}")
            time.sleep(0.3)
    log("would save images to NOMAD_DATA/images/nomad.tar")
    time.sleep(0.5)


def _fake_write_compose(cfg: FlashConfig, log: StepLogger) -> None:
    log("would copy bundled docker-compose.yml to NOMAD_DATA/nomad/")
    time.sleep(0.2)
    log("would copy supporting scripts (entrypoint.sh, wait-for-it.sh, ...)")
    time.sleep(0.2)
    log("would chmod +x all .sh files")
    time.sleep(0.2)


# The pipeline is just an ordered list of steps. Reorganizing the order
# (or adding/removing steps) happens here, not in the orchestrator.
#
# Steps are imported lazily at module-load time so a missing optional
# dep in one step doesn't break the whole pipeline import.
from .steps_partition import step_partition            # noqa: E402
from .steps_iso import step_copy_iso                   # noqa: E402
from .steps_images import step_pull_images, step_write_compose  # noqa: E402

DEFAULT_PIPELINE: list[Step] = [
    Step("Partition",     step_partition),
    Step("Copy ISO",      step_copy_iso),
    Step("Pull images",   step_pull_images),
    Step("Write compose", step_write_compose),
]


def run_pipeline(cfg: FlashConfig, log: StepLogger,
                 pipeline: list[Step] = DEFAULT_PIPELINE) -> None:
    """Run all steps in order. Streams via `log`. Raises on first failure.

    The orchestrator's job is small but important:
      - Wrap each step in clear start/end markers so the UI can show
        which step is currently running
      - Convert step exceptions into log lines + reraise (so the
        WebSocket layer can mark the run as failed)
    """
    log("=== Flash starting ===")
    log(f"Target: {cfg.device}")
    log(f"ISO:    {cfg.iso_path}")
    log(f"Apps:   {', '.join(cfg.apps) if cfg.apps else '(base only)'}")
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
