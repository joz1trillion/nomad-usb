"""Pipeline steps: pull docker images and write compose config.

Two steps in one module because they're closely related — both
populate NOMAD_DATA with what Nomad needs to start.

step_pull_images:
  - If cfg.archive_path is set, skip pulling and use that tarball
  - Otherwise, make sure each image is present in the local Docker
    daemon (pulling if missing, or always pulling if cfg.no_cache)
  - Save all images to /mnt/nomad-data/images/nomad.tar so the live
    system can `docker load` them offline at first boot

step_write_compose:
  - Mount NOMAD_DATA
  - Create the expected directory tree (nomad/, nomad/storage/, etc.)
  - Copy our bundled docker-compose.yml to nomad/docker-compose.yml

Both steps are idempotent — re-running them over an existing flash
just overwrites what's there.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .runner import run, which, StepLogger
from .steps_partition import _partition_path

if TYPE_CHECKING:
    from .flash_pipeline import FlashConfig


# The core images every Nomad install needs, regardless of which
# optional apps are selected. Tags here match what's in
# nomad_flash/bundled/docker-compose.yml — keep them in sync if
# compose template changes.
BASE_IMAGES: list[str] = [
    "ghcr.io/crosstalk-solutions/project-nomad:latest",
    "mysql:8.0",
    "redis:7-alpine",
    "amir20/dozzle:v10.0",
]


# Directories the live system expects to exist on NOMAD_DATA. The
# nomad-mount systemd service creates a few of these on first boot
# too, but creating them here means the USB is "done" after flashing
# even before anything runs on it. Harmless to pre-create what the
# service would later create anyway.
NOMAD_DATA_DIRS: list[str] = [
    "nomad",
    "nomad/storage",
    "nomad/mysql",
    "nomad/redis",
    "images",
    "ssh-host-keys",
]


# ---------- image pulling ----------

def _image_exists_locally(image: str, log: StepLogger) -> bool:
    """Return True if `image` is already in the local docker daemon."""
    # `docker image inspect` exits 0 if the image exists, 1 if not.
    # We swallow its output because failure is normal here.
    rc = run([which("docker"), "image", "inspect", image],
             log, check=False, prefix="  ? ")
    return rc == 0


def _pull_image(image: str, log: StepLogger) -> None:
    """docker pull `image`, streaming progress to the log."""
    # docker pull streams its own progress (layer status, percentages)
    # which our run() helper pipes straight to the log.
    run([which("docker"), "pull", image], log)


def _save_images(images: list[str], out_path: str, log: StepLogger) -> None:
    """docker save one or more images to a single tarball.

    `docker save` accepts multiple image refs and produces one combined
    archive containing all of them. The live system does `docker load`
    on the tarball at first boot to rehydrate them without internet.
    """
    cmd = [which("docker"), "save", "-o", out_path, *images]
    run(cmd, log)


def step_pull_images(cfg: "FlashConfig", log: StepLogger) -> None:
    """Ensure all needed images are in the local daemon, then save
    them to /mnt/nomad-data/images/nomad.tar on the USB."""

    # Build the full image list: base set + images from any optional
    # apps the user selected. apps_catalog handles the key→images
    # mapping (some apps map to multiple images, e.g. AI = ollama+qdrant).
    from .apps_catalog import images_for_apps
    images = list(BASE_IMAGES)
    if cfg.apps:
        try:
            extra = images_for_apps(cfg.apps)
        except KeyError as e:
            raise RuntimeError(f"unknown app: {e}") from e
        log(f"adding {len(extra)} image(s) for {len(cfg.apps)} optional app(s)")
        images.extend(extra)

    # Partition 4 is NOMAD_DATA (p1=BIOS_BOOT, p2=EFI, p3=NOMAD_LIVE).
    p_data = _partition_path(cfg.device, 4)

    data_mnt = tempfile.mkdtemp(prefix="nomad-data-")
    mounted = False

    try:
        log(f"mounting {p_data} (NOMAD_DATA) at {data_mnt}")
        run([which("mount"), p_data, data_mnt], log)
        mounted = True

        images_dir = Path(data_mnt) / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        out_tar = images_dir / "nomad.tar"

        # ---- handle --from-archive: skip pulling, just copy ----
        if cfg.archive_path:
            src = Path(cfg.archive_path).resolve()
            if not src.is_file():
                raise RuntimeError(f"archive not found: {src}")
            log(f"copying pre-built archive {src} -> {out_tar}")
            # shutil.copy2 preserves metadata and is faster than
            # shelling out to cp for large files.
            shutil.copy2(src, out_tar)
            log("archive copy complete")
            return

        # ---- normal path: ensure images are local, then save ----
        for image in images:
            if cfg.no_cache:
                log(f"pulling {image} (no-cache: forced)")
                _pull_image(image, log)
            elif _image_exists_locally(image, log):
                log(f"using cached {image}")
            else:
                log(f"pulling {image}")
                _pull_image(image, log)

        log(f"saving {len(images)} image(s) -> {out_tar}")
        _save_images(images, str(out_tar), log)

        # Report the archive size so the user has a rough idea what
        # got written. Useful for sanity-checking — if it's 0 bytes
        # or suspiciously small, something went sideways.
        size_mb = out_tar.stat().st_size / (1024 * 1024)
        log(f"wrote {size_mb:.1f} MB to {out_tar.name}")

    finally:
        if mounted:
            run([which("umount"), data_mnt], log, check=False)
        try:
            shutil.rmtree(data_mnt, ignore_errors=True)
        except OSError:
            pass


# ---------- compose file + directory tree ----------

def _bundled_compose_path() -> Path:
    """Locate our bundled docker-compose.yml inside the package.

    The flash tool ships this file at
    nomad_flash/bundled/docker-compose.yml so it's available without
    needing to mount the source ISO.
    """
    # __file__ is .../nomad_flash/steps_images.py, so the bundled dir
    # is a sibling directory.
    pkg_dir = Path(__file__).resolve().parent
    compose = pkg_dir / "bundled" / "docker-compose.yml"
    if not compose.is_file():
        raise RuntimeError(
            f"bundled docker-compose.yml missing at {compose} — "
            f"is the package install intact?"
        )
    return compose


def step_write_compose(cfg: "FlashConfig", log: StepLogger) -> None:
    """Copy docker-compose.yml to NOMAD_DATA and pre-create app dirs."""

    p_data = _partition_path(cfg.device, 4)
    compose_src = _bundled_compose_path()

    data_mnt = tempfile.mkdtemp(prefix="nomad-data-")
    mounted = False

    try:
        log(f"mounting {p_data} (NOMAD_DATA) at {data_mnt}")
        run([which("mount"), p_data, data_mnt], log)
        mounted = True

        # ---- create the directory tree ----
        root = Path(data_mnt)
        for d in NOMAD_DATA_DIRS:
            path = root / d
            path.mkdir(parents=True, exist_ok=True)
            log(f"  ensured {d}/")

        # ---- write the compose file ----
        compose_dest = root / "nomad" / "docker-compose.yml"
        log(f"writing {compose_dest}")
        shutil.copy2(compose_src, compose_dest)

        log("compose and directory tree complete")

    finally:
        if mounted:
            run([which("umount"), data_mnt], log, check=False)
        try:
            shutil.rmtree(data_mnt, ignore_errors=True)
        except OSError:
            pass
