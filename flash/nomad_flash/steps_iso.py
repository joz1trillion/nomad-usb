"""Pipeline step: copy live ISO contents onto NOMAD_LIVE and install GRUB.

Inputs (from cfg):
  iso_path  the .iso file the user picked
  device    the target disk, e.g. /dev/sdb (we derive partition paths)

What it does:
  1. Mount the ISO read-only via a loop device
  2. Mount NOMAD_LIVE (p3) and EFI (p2) at temp dirs
  3. rsync the ISO's contents onto NOMAD_LIVE
  4. Install GRUB for UEFI (target=x86_64-efi) → EFI partition
  5. Install GRUB for legacy BIOS (target=i386-pc) → MBR of the disk,
     embedding core.img into p1 (BIOS_BOOT)
  6. Write a grub.cfg that boots the live system from NOMAD_LIVE

After this step, the USB is bootable but has no Nomad data on
NOMAD_DATA yet. That's the next step (3d).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .runner import run, which, StepLogger
from .steps_partition import _partition_path

if TYPE_CHECKING:
    from .flash_pipeline import FlashConfig


# What we tell GRUB to put on the kernel command line.
# Matches what live-build sets in auto/config (--bootappend-live):
#   boot=live      tells live-boot to mount the squashfs
#   components     enables live-config customizations
#
# We deliberately keep boot output VISIBLE (no quiet, no loglevel=3).
# This is a server box that gets booted on unfamiliar hardware in
# unknown network conditions. When something goes wrong — and it will —
# the user needs to see what systemd is doing, what services are
# failing, and what the kernel has to say about the disk and the
# network. A pretty silent boot is worse than a noisy informative one.
#
# We also do NOT pass `toram`. live-boot's `toram` mode rsync's the
# ~800MB squashfs into RAM at every boot, which (a) prints scrolling
# rsync progress to /dev/console (looks like garbage), and (b) eats
# 1GB of RAM that docker containers could be using. The squashfs
# contains the base Debian system; docker reads from its own layers
# in /mnt/nomad-data/docker/ which is a separate ext4 partition, so
# the squashfs being on USB vs in RAM barely matters at runtime.
GRUB_BOOTAPPEND = "boot=live components"


def _grub_cfg(label_live: str = "NOMAD_LIVE") -> str:
    """The grub.cfg we write to NOMAD_LIVE/boot/grub/grub.cfg.

    Two menu entries: normal and "safe graphics" (nomodeset) for
    machines where the GPU driver doesn't come up cleanly. The
    `search --set=root --label NOMAD_LIVE` line is what makes this
    portable — GRUB finds the partition by its label, not by a
    hardcoded /dev path or UUID, so the same configuration works
    regardless of which slot the USB lands in on the target machine.

    Kernel and initrd filenames: Debian live-build installs these
    with a version suffix (e.g. /live/vmlinuz-6.12.74+deb13+1-amd64),
    not a bare 'vmlinuz'. We use GRUB's `for f in /live/vmlinuz-*`
    loop to discover whatever version is actually there at boot
    time. This keeps the config working across kernel updates
    without needing to hardcode a version.

    The `regexp` module gives GRUB globbing; `insmod regexp` makes
    it available. Each `for` loop finds at most one file (there's
    only one kernel in the live image) and assigns it to a variable
    used by the linux/initrd commands.
    """
    return f"""\
set timeout=5
set default=0

insmod ext2
insmod part_gpt
insmod regexp

# Find the live partition by filesystem label.
search --no-floppy --label {label_live} --set=root

# Discover the versioned kernel + initrd filenames at boot time so
# we don't have to hardcode them. live-build names them like
# vmlinuz-6.12.74+deb13+1-amd64, not plain vmlinuz. `for` iterates
# glob matches; since there's only one kernel, the loop body runs
# once with $kernel / $initrd set to the real paths.
for f in /live/vmlinuz-*; do set kernel="$f"; done
for f in /live/initrd.img-*; do set initrd_file="$f"; done

menuentry "Project Nomad" {{
    linux $kernel {GRUB_BOOTAPPEND} username=nomad hostname=nomad
    initrd $initrd_file
}}

menuentry "Project Nomad (safe graphics)" {{
    linux $kernel {GRUB_BOOTAPPEND} username=nomad hostname=nomad nomodeset
    initrd $initrd_file
}}
"""


def _mount(source: str, target: str, log: StepLogger,
           options: list[str] | None = None) -> None:
    """Mount with a clean argv."""
    cmd = [which("mount")]
    if options:
        cmd += ["-o", ",".join(options)]
    cmd += [source, target]
    run(cmd, log)


def _umount(target: str, log: StepLogger) -> None:
    """Unmount, but don't fail the whole pipeline if it's already gone."""
    run([which("umount"), target], log, check=False)


def step_copy_iso(cfg: "FlashConfig", log: StepLogger) -> None:
    """Mount cfg.iso_path and copy its contents to NOMAD_LIVE; install GRUB."""

    iso = Path(cfg.iso_path).resolve()
    if not iso.is_file():
        raise RuntimeError(f"ISO not found: {iso}")

    # Partition layout (post step_partition):
    #   p1 BIOS_BOOT (untouched here, only grub-install needs it)
    #   p2 EFI
    #   p3 NOMAD_LIVE
    #   p4 NOMAD_DATA
    p_efi = _partition_path(cfg.device, 2)
    p_live = _partition_path(cfg.device, 3)

    # Three temporary mount points. tempfile.mkdtemp gives us guaranteed-
    # unique paths so we don't collide with anything else.
    iso_mnt = tempfile.mkdtemp(prefix="nomad-iso-")
    live_mnt = tempfile.mkdtemp(prefix="nomad-live-")
    efi_mnt = tempfile.mkdtemp(prefix="nomad-efi-")

    # Track what we mounted so the cleanup block can be exhaustive
    # even if we fail partway through.
    mounted: list[str] = []

    try:
        # ---- step 1: mount the ISO ----
        log(f"mounting ISO {iso}")
        _mount(str(iso), iso_mnt, log, options=["loop", "ro"])
        mounted.append(iso_mnt)

        # ---- step 2: mount the target partitions ----
        log(f"mounting {p_live} (NOMAD_LIVE) at {live_mnt}")
        _mount(p_live, live_mnt, log)
        mounted.append(live_mnt)

        log(f"mounting {p_efi} (EFI) at {efi_mnt}")
        _mount(p_efi, efi_mnt, log)
        mounted.append(efi_mnt)

        # ---- step 3: rsync ISO contents → NOMAD_LIVE ----
        # -a    archive (preserve perms, links, times, owner, group)
        # -H    preserve hardlinks (important for some live-boot trees)
        # -X    preserve extended attributes (e.g. SELinux contexts)
        # --info=progress2  human-readable progress with ETA
        # --no-i-r          turn off "incremental recursion" so the
        #                   progress bar shows total progress, not
        #                   "files seen so far"
        log("copying ISO contents to NOMAD_LIVE")
        run([
            which("rsync"),
            "-aHX",
            "--info=progress2",
            "--no-inc-recursive",
            f"{iso_mnt}/",  # trailing slash: copy contents, not the dir itself
            f"{live_mnt}/",
        ], log)

        # ---- step 4: GRUB for UEFI ----
        # --target=x86_64-efi  build the EFI version of the loader
        # --efi-directory      where the EFI partition is mounted
        # --boot-directory     where /boot/grub will live (on NOMAD_LIVE)
        # --removable          install at /EFI/BOOT/BOOTX64.EFI which
        #                      most firmwares boot without needing
        #                      an NVRAM entry — critical for portability
        # --no-nvram           don't try to register an NVRAM entry
        #                      (we're flashing a USB, not installing
        #                      to this machine, so polluting its NVRAM
        #                      is wrong)
        # --recheck            force a fresh device scan
        log("installing GRUB for UEFI boot")
        run([
            which("grub-install"),
            "--target=x86_64-efi",
            f"--efi-directory={efi_mnt}",
            f"--boot-directory={live_mnt}/boot",
            "--removable",
            "--no-nvram",
            "--recheck",
        ], log)

        # ---- step 5: GRUB for legacy BIOS ----
        # --target=i386-pc     traditional BIOS boot
        # --boot-directory     where /boot/grub lives
        # --force              bypass the "multiple partition labels" warning.
        #                      This comes up on USBs that have both a GPT
        #                      AND a protective MBR (which is normal/correct)
        #                      but grub-install treats as suspicious. We know
        #                      the BIOS boot partition exists (p1, type ef02)
        #                      and have verified it's aligned on a 2048-sector
        #                      boundary — force skips the sanity check that's
        #                      blocking us.
        # The disk path (cfg.device) goes at the end as the install
        # target — this writes the boot code to the disk's MBR.
        log("installing GRUB for BIOS boot")
        run([
            which("grub-install"),
            "--target=i386-pc",
            f"--boot-directory={live_mnt}/boot",
            "--force",
            "--recheck",
            cfg.device,
        ], log)

        # ---- step 6: write our grub.cfg ----
        # `grub-install` doesn't generate a config — it just installs
        # the loader. We have to put the menu entries there ourselves.
        log("writing grub.cfg")
        grub_dir = Path(live_mnt) / "boot" / "grub"
        grub_dir.mkdir(parents=True, exist_ok=True)
        (grub_dir / "grub.cfg").write_text(_grub_cfg())

        log("ISO copy and GRUB install complete")

    finally:
        # ---- cleanup: unmount in reverse order ----
        # We unmount everything we mounted, even if the body raised.
        # Reverse order so children come off before parents (not
        # strictly necessary here since they're separate, but a good
        # habit).
        for mnt in reversed(mounted):
            _umount(mnt, log)

        # Remove the now-empty mount points.
        for mnt in (iso_mnt, live_mnt, efi_mnt):
            try:
                shutil.rmtree(mnt, ignore_errors=True)
            except OSError:
                pass
