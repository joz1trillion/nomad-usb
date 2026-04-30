"""Pipeline step: partition the target device.

Given /dev/sdX, produce a 3-partition GPT layout:

  p1   512MB    EFI System  (FAT32)  label EFI
  p2   6GB      Linux fs    (ext4)   label NOMAD_LIVE
  p3   rest     Linux fs    (ext4)   label NOMAD_DATA

This wipes everything on the device. Anything that was there before
is gone — that's the whole point.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .runner import run, which, StepLogger

if TYPE_CHECKING:
    # Only imported for type hints. At runtime FlashConfig comes
    # through `cfg` as a duck-typed object — we just access fields.
    # This avoids the circular import: flash_pipeline imports us
    # to register the step, and we'd otherwise import it back to
    # type-annotate cfg.
    from .flash_pipeline import FlashConfig


# Sizes for the fixed-size partitions. NOMAD_DATA gets all remaining
# space, so its size depends on the USB.
#
# BIOS_BOOT is a tiny partition required for legacy BIOS booting from
# a GPT disk. It holds GRUB's embedded core.img. We don't put a
# filesystem on it — type code ef02 marks it as the BIOS Boot Partition
# and grub-install knows what to do with it.
BIOS_BOOT_SIZE = "1M"
EFI_SIZE = "512M"
LIVE_SIZE = "6G"


def _partition_path(device: str, n: int) -> str:
    """Resolve /dev/sdb + 1 → /dev/sdb1, /dev/nvme0n1 + 1 → /dev/nvme0n1p1.

    Block device naming doesn't have a single rule. Devices whose name
    ends in a digit (NVMe, MMC, virtio) get a 'p' separator before the
    partition number; others (SATA/SCSI/USB) just append the number.
    """
    if device[-1].isdigit():
        return f"{device}p{n}"
    return f"{device}{n}"


def _wait_for_block(path: str, timeout: float = 5.0) -> None:
    """Block until `path` shows up as a block device, up to `timeout` sec.

    After partprobe / sgdisk, the kernel takes a moment to publish the
    new partition device nodes via udev. Without this wait, immediately
    running mkfs.* on the partition can hit "no such device" sporadically.
    """
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if os.path.exists(path):
            return
        time.sleep(0.1)
    raise RuntimeError(f"timed out waiting for {path} to appear")


def _unmount_all(device: str, log: StepLogger) -> None:
    """Best-effort unmount of every partition on `device`.

    `dd`/`sgdisk` will refuse to write to a device with mounted
    partitions on it. We don't fail if a partition isn't mounted —
    that's the normal case.
    """
    # Look at /proc/mounts to find every mounted partition off this disk.
    try:
        with open("/proc/mounts") as f:
            mounts = f.readlines()
    except OSError:
        return

    targets: list[str] = []
    for line in mounts:
        src = line.split(" ", 1)[0]
        if src.startswith(device):
            targets.append(src)

    for src in targets:
        log(f"unmounting {src}")
        # check=False: if umount fails (already gone, busy, etc.) keep
        # trying the rest. We'll fail at the next destructive step if
        # something genuinely can't be detached.
        run([which("umount"), src], log, check=False)


def step_partition(cfg: FlashConfig, log: StepLogger) -> None:
    """Wipe `cfg.device` and create the 3-partition layout."""
    device = cfg.device

    # Sanity guard — refuse if the path doesn't look like a block device.
    # A typo here ("/dev/sda1" vs "/dev/sda") is the kind of thing that
    # destroys data. We require the path to exist, be a block device,
    # and NOT itself be a partition.
    p = Path(device)
    if not p.exists():
        raise RuntimeError(f"device does not exist: {device}")
    if not p.is_block_device():
        raise RuntimeError(f"not a block device: {device}")
    # Heuristic: a partition name ends in a digit (sda1, nvme0n1p1).
    # We want a whole disk (sda, nvme0n1). NVMe disk names ALSO end in
    # a digit, but they don't have a 'p' before the trailing digit.
    name = p.name
    if name[-1].isdigit() and "p" in name[-3:-1]:
        raise RuntimeError(f"target looks like a partition, not a disk: {device}")

    # ---- step 1: detach anything currently using the device ----
    _unmount_all(device, log)

    # ---- step 2: wipe the head of the disk ----
    # Belt-and-suspenders zeroing of the first MB, then sgdisk on top.
    # Some USBs come with weird hybrid MBR/GPT structures, leftover
    # isohybrid data from previous flashes, or Windows-recovery partition
    # tables that `sgdisk --zap-all` alone doesn't fully clear and
    # grub-install later sees as "multiple partition labels" / hybrid.
    # Zeroing first removes all doubt.
    log("zeroing first MB of disk")
    run([
        which("dd"),
        "if=/dev/zero",
        f"of={device}",
        "bs=1M", "count=1", "conv=notrunc,fsync",
    ], log)

    # ---- step 3: zap any existing partition table ----
    # --zap-all wipes both the primary GPT and the backup GPT (at the
    # end of the disk). After step 2, the primary is already gone,
    # but the backup GPT at the end of the disk can still trip up
    # grub-install — this call cleans that too.
    #
    # check=False: sgdisk returns exit code 2 when it finds the disk
    # in an inconsistent state (main header missing but backup present,
    # which is exactly what we just created by zeroing the start of
    # the disk). It still performs the zap correctly in that case —
    # the exit code reflects "you might want to investigate," not
    # "I failed to do what you asked." The subsequent sgdisk --new
    # calls will fail if the zap didn't actually work, which is a
    # better signal than sgdisk's somewhat panicky exit code here.
    log("wiping existing partition table")
    run([which("sgdisk"), "--zap-all", device], log, check=False)

    # ---- step 4: create the four partitions ----
    # sgdisk syntax: --new=N:start:end where 0 means "default" — for
    # start that's "first available sector", for end with +SIZE that's
    # "this many bytes from start", for end "0" that's "rest of disk".
    #
    # typecodes:
    #   ef02 = BIOS Boot Partition (gives grub-install --target=i386-pc
    #          a place to embed core.img on a GPT disk)
    #   ef00 = EFI System Partition (lets firmware identify it)
    #   8300 = Linux filesystem (generic)
    #
    # change-name sets the GPT partition label (different from filesystem
    # label that mkfs sets later — we set both for redundancy).
    #
    # Layout: p1 BIOS_BOOT, p2 EFI, p3 NOMAD_LIVE, p4 NOMAD_DATA.
    # The downstream code refers to p2/p3/p4 (NOT p1) — the BIOS boot
    # partition is invisible after creation, only grub-install touches it.
    log("creating partition table")
    run([
        which("sgdisk"),
        f"--new=1:0:+{BIOS_BOOT_SIZE}", "--typecode=1:ef02", "--change-name=1:BIOS_BOOT",
        f"--new=2:0:+{EFI_SIZE}",       "--typecode=2:ef00", "--change-name=2:EFI",
        f"--new=3:0:+{LIVE_SIZE}",      "--typecode=3:8300", "--change-name=3:NOMAD_LIVE",
        "--new=4:0:0",                  "--typecode=4:8300", "--change-name=4:NOMAD_DATA",
        device,
    ], log)

    # Set the "legacy BIOS bootable" GPT attribute on p1. Some
    # grub-install versions check this flag (not just the type code)
    # when looking for the BIOS boot partition to embed core.img into.
    # sgdisk attribute 2 == legacy BIOS bootable.
    log("marking BIOS_BOOT partition as legacy-bootable")
    run([which("sgdisk"), "--attributes=1:set:2", device], log)

    # ---- step 5: ask kernel to re-read the new table ----
    # partprobe pokes the kernel to refresh its in-memory partition
    # table. Without this, /dev/sdb1 etc. may not exist yet.
    log("notifying kernel of partition changes")
    run([which("partprobe"), device], log, check=False)
    # And give udev a moment to actually create the device nodes.
    run([which("udevadm"), "settle"], log, check=False)

    # p1 is the BIOS boot partition — never mounted, never formatted,
    # only touched by grub-install. We skip it here.
    p2 = _partition_path(device, 2)  # EFI
    p3 = _partition_path(device, 3)  # NOMAD_LIVE
    p4 = _partition_path(device, 4)  # NOMAD_DATA
    for p in (p2, p3, p4):
        _wait_for_block(p)

    # ---- step 6: format ----
    # -F  force (overwrite any existing fs signature without prompting)
    # -L  filesystem label — matters because the live system mounts
    #     NOMAD_DATA by label, and the EFI loader looks for an EFI label.
    log(f"formatting {p2} as FAT32 (EFI)")
    run([which("mkfs.vfat"), "-F", "32", "-n", "EFI", p2], log)

    log(f"formatting {p3} as ext4 (NOMAD_LIVE)")
    run([which("mkfs.ext4"), "-F", "-L", "NOMAD_LIVE", p3], log)

    log(f"formatting {p4} as ext4 (NOMAD_DATA)")
    run([which("mkfs.ext4"), "-F", "-L", "NOMAD_DATA", p4], log)

    log(f"partition complete: {p2} (EFI), {p3} (NOMAD_LIVE), {p4} (NOMAD_DATA)")
