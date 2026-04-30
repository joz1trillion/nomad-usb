"""Block device enumeration.

We shell out to lsblk because:
  - It already does the hard work (device discovery, sizes, removable
    flag, transport type, holders).
  - Its JSON output is stable and well-documented.
  - Reading /sys/block ourselves duplicates work and adds bugs.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class Device:
    """A flashable block device.

    Fields kept deliberately small — anything else lsblk knows can
    be added later without schema churn.
    """
    name: str          # e.g. "sdb"  (no /dev prefix)
    size: str          # human readable, e.g. "64G"
    model: str         # vendor/model string, e.g. "SanDisk Ultra"
    tran: str          # transport: "usb", "sata", "nvme", ...
    removable: bool    # /sys/block/<name>/removable == "1"
    rotational: bool   # spinning rust vs solid-state


def _lsblk() -> dict:
    """Run lsblk and return its parsed JSON output.

    -d  list disks only (no partitions/holders in the top-level array)
    -n  no header line — JSON output uses field names anyway, this is
        belt-and-suspenders
    -J  JSON output
    -o  the columns we need, in order
    """
    cmd = [
        "lsblk", "-d", "-n", "-J",
        "-o", "NAME,SIZE,MODEL,TRAN,RM,RO,ROTA,TYPE",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=10,
        )
    except FileNotFoundError as e:
        raise RuntimeError("lsblk not installed; install util-linux") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"lsblk failed: {e.stderr.strip()}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("lsblk timed out") from e

    return json.loads(result.stdout)


def _detect_live_device() -> str | None:
    """If we're running from a live USB, return the device name to
    exclude from the flashable list. Returns None if not on live media.

    Detection: /run/live/medium is the standard live-boot mount point.
    """
    medium = Path("/run/live/medium")
    if not medium.exists():
        return None

    # Find the source device backing this mount point.
    try:
        result = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", str(medium)],
            capture_output=True, text=True, check=True, timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError):
        return None

    src = result.stdout.strip()
    if not src:
        return None

    # src is e.g. /dev/sda2 — get the parent disk name (sda).
    try:
        result = subprocess.run(
            ["lsblk", "-no", "PKNAME", src],
            capture_output=True, text=True, check=True, timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None

    parent = result.stdout.strip()
    return parent or None


def list_devices() -> list[Device]:
    """List flashable block devices, filtered and sorted.

    Filtering:
      - Skip read-only devices
      - Skip non-disk types (loop, rom, cdrom)
      - Skip the live boot device (we'd be sawing off the branch we sit on)

    Sorting:
      - Removable devices first (USB sticks are usually what users want)
      - Then by name for stable ordering
    """
    raw = _lsblk()
    live_dev = _detect_live_device()

    devices: list[Device] = []
    for d in raw.get("blockdevices", []):
        # Skip things that aren't real disks. lsblk's -d already
        # excludes most non-disks, but loop devices and roms can
        # still slip in depending on system state.
        if d.get("type") not in (None, "disk"):
            continue
        # Read-only devices can't be flashed. lsblk reports "0" / "1".
        if str(d.get("ro", "0")) == "1":
            continue
        # Don't list the device we're booted from.
        if live_dev and d["name"] == live_dev:
            continue

        devices.append(Device(
            name=d["name"],
            size=d.get("size") or "?",
            model=(d.get("model") or "").strip(),
            tran=d.get("tran") or "",
            removable=str(d.get("rm", "0")) == "1",
            rotational=str(d.get("rota", "0")) == "1",
        ))

    # Sort: removable first, then alphabetical. The wizard shows
    # whatever order we return, so doing it here keeps the frontend
    # purely presentational.
    devices.sort(key=lambda d: (not d.removable, d.name))
    return devices


def list_devices_dict() -> list[dict]:
    """Same as list_devices() but returns plain dicts for JSON serialization."""
    return [asdict(d) for d in list_devices()]
