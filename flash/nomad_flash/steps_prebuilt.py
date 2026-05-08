"""Pipeline step: deploy a prebuilt docker data tree onto NOMAD_DATA.

This is an alternative to step_pull_images. Instead of saving images
as a tarball that the live system then `docker load`s on first boot
(slow — 15-30 min on USB), we extract a pre-built docker storage
directory directly onto NOMAD_DATA at flash time. The live system
boots, docker starts, and finds an already-populated overlay2 store.
First boot becomes "fast" — just compose-up time, no image unpack.

The tarball can be either gzip (.tar.gz) or xz (.tar.xz). xz is the
default for v1 release artifacts because it compresses ~30% better,
which matters when GitHub release assets are capped at 2GB.
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


# Subdirectories every well-formed /var/lib/docker tree should have.
# We sanity-check for these after extraction so a corrupted or
# mis-built golden tarball is caught here, not at first boot.
EXPECTED_DOCKER_SUBDIRS: list[str] = [
    "overlay2",
    "image",
    "containers",
]


def _detect_compression(path: Path) -> str:
    """Return the tar flag for this archive's compression.

    We sniff the magic bytes rather than trusting the extension because
    a file named .tar.gz could actually be xz (e.g. user renamed it).
    Wrong extension is a footgun we'd rather catch up front.

    Returns one of: 'z' (gzip), 'J' (xz), 'j' (bzip2).
    """
    with path.open("rb") as f:
        head = f.read(6)

    # gzip: 1f 8b
    if head[:2] == b"\x1f\x8b":
        return "z"
    # xz: fd 37 7a 58 5a 00
    if head[:6] == b"\xfd7zXZ\x00":
        return "J"
    # bzip2: 42 5a 68
    if head[:3] == b"BZh":
        return "j"
    raise RuntimeError(
        f"can't determine compression of {path}: "
        f"first 6 bytes are {head!r}; expected gzip, xz, or bzip2 magic"
    )


def _destination_already_populated(data_mnt: Path) -> bool:
    """Detect whether NOMAD_DATA/docker is already a real docker tree.

    Used to avoid silently clobbering a previously-flashed USB. We
    define "already populated" as: docker/overlay2 exists and contains
    at least one entry. An empty docker/ dir doesn't count.
    """
    overlay = data_mnt / "docker" / "overlay2"
    if not overlay.is_dir():
        return False
    try:
        next(overlay.iterdir())
        return True
    except (StopIteration, PermissionError):
        return False


def step_deploy_prebuilt_docker(cfg: "FlashConfig", log: StepLogger) -> None:
    """Replace step_pull_images for prebuilt mode.

    Extracts cfg.prebuilt_docker_path onto NOMAD_DATA at /docker/,
    skipping the docker save → tarball → first-boot load roundtrip.
    """
    if not cfg.prebuilt_docker_path:
        raise RuntimeError(
            "step_deploy_prebuilt_docker called without "
            "cfg.prebuilt_docker_path set"
        )

    src = Path(cfg.prebuilt_docker_path).expanduser().resolve()

    # Cheap sanity checks: file exists, magic bytes match a known
    # tar compression. We deliberately do NOT do `tar -tf` here to
    # list every file — for a 4.4GB docker tree that's hundreds of
    # thousands of lines, which (a) takes a while, (b) blows the
    # WebSocket log buffer past gigabytes, and (c) was the cause of
    # the v0.6.0 25GB-RAM browser-tab problem. If the tarball is
    # corrupt, the extraction itself will fail fast with a useful
    # error from tar — which is a better signal than a pre-check
    # would have given us anyway.
    if not src.is_file():
        raise RuntimeError(f"prebuilt docker tarball not found: {src}")

    size_mb = src.stat().st_size / (1024 * 1024)
    log(f"prebuilt tarball: {src} ({size_mb:.1f} MB)")

    tar_flag = _detect_compression(src)
    log(f"compression: {'gzip' if tar_flag == 'z' else 'xz' if tar_flag == 'J' else 'bzip2'}")

    p_data = _partition_path(cfg.device, 4)
    data_mnt_str = tempfile.mkdtemp(prefix="nomad-data-")
    data_mnt = Path(data_mnt_str)
    mounted = False

    try:
        log(f"mounting {p_data} (NOMAD_DATA) at {data_mnt}")
        run([which("mount"), p_data, data_mnt_str], log)
        mounted = True

        if _destination_already_populated(data_mnt):
            raise RuntimeError(
                f"NOMAD_DATA at {p_data} already has a populated "
                f"docker/ tree. Refusing to overwrite. To re-flash, "
                f"wipe NOMAD_DATA first."
            )

        data_mnt.mkdir(parents=True, exist_ok=True)

        log(f"extracting {src.name} -> {data_mnt}/docker/")
        log("(this is the bulk of flash time — a few minutes for ~5GB)")

        # tar flags:
        #   -x  extract
        #   tar_flag is one of z/J/j (gzip/xz/bzip2) — sniffed earlier
        #   -p  preserve perms (CRITICAL — docker overlay2 needs
        #       specific ownership/perms or the daemon will refuse
        #       to mount layers)
        #   --same-owner  preserve uid/gid
        #   -f  input file
        #   -C  change to this dir before extracting
        #
        # We do NOT pass any flag that produces per-file output
        # (no -v). For 100k+ files the output would crush the UI
        # and cost a lot of memory.
        run(
            [
                which("tar"),
                f"-x{tar_flag}pf",
                str(src),
                "--same-owner",
                "-C",
                data_mnt_str,
            ],
            log,
        )

        docker_dir = data_mnt / "docker"
        if not docker_dir.is_dir():
            raise RuntimeError(
                f"extraction succeeded but {docker_dir} is missing — "
                f"was the tarball built from the right directory?"
            )

        missing = [
            d for d in EXPECTED_DOCKER_SUBDIRS
            if not (docker_dir / d).is_dir()
        ]
        if missing:
            raise RuntimeError(
                f"extracted docker/ tree is missing expected "
                f"subdirectories: {', '.join(missing)}. The tarball "
                f"may not be a complete /var/lib/docker copy."
            )

        marker = data_mnt / "docker" / ".nomad-prebuilt"
        marker.write_text(
            "this NOMAD_DATA was flashed with a prebuilt docker tree; "
            "nomad-init should skip image loading\n"
        )

        layer_count = sum(1 for _ in (docker_dir / "overlay2").iterdir())
        log(f"deployed prebuilt docker tree: {layer_count} overlay2 layers")

    finally:
        if mounted:
            run([which("umount"), data_mnt_str], log, check=False)
        try:
            shutil.rmtree(data_mnt_str, ignore_errors=True)
        except OSError:
            pass
