#!/bin/bash
#
# test-flash.sh — Flash a USB with the Nomad ISO + NOMAD_DATA partition.
#
# THIS IS A TESTING SCRIPT. It is NOT the final flash tool.
# It exists to prove the full boot-to-Nomad pipeline end-to-end
# before we invest time in building the polished flash tool.
#
# Strategy:
#   1. dd the hybrid ISO to the whole device (preserves its bootloader)
#   2. Expand GPT to the full disk (dd only writes ISO-sized table)
#   3. Add NOMAD_DATA partition in the free space
#   4. Optionally populate NOMAD_DATA with Nomad files + pre-pulled images
#
# Usage:
#   sudo ./test-flash.sh --iso PATH --device /dev/sdX [--no-populate]
#
# Requires on host: sgdisk, dd, mkfs.ext4, partprobe, lsblk, mount, umount
# For --populate:  docker, git

set -euo pipefail

# -------- defaults --------
ISO=""
DEVICE=""
POPULATE=true
NOMAD_REPO="https://github.com/Crosstalk-Solutions/project-nomad.git"

usage() {
    cat <<EOF
Usage: sudo $0 --iso PATH --device /dev/sdX [--no-populate]

  --iso PATH           Path to the built live-image-amd64.hybrid.iso
  --device /dev/sdX    Target USB device (WILL BE WIPED — triple-check!)
  --no-populate        Skip the part that pulls docker images and clones
                       the Nomad repo onto the data partition. Fastest
                       way to test just the boot + service chain.

Example:
  sudo $0 --iso output/live-image-amd64.hybrid.iso --device /dev/sdb
EOF
    exit 1
}

# -------- arg parsing --------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --iso)          ISO="$2"; shift 2 ;;
        --device)       DEVICE="$2"; shift 2 ;;
        --no-populate)  POPULATE=false; shift ;;
        -h|--help)      usage ;;
        *) echo "Unknown arg: $1" >&2; usage ;;
    esac
done

[[ -z "$ISO" || -z "$DEVICE" ]] && usage
[[ ! -f "$ISO"    ]] && { echo "ISO not found: $ISO" >&2; exit 1; }
[[ ! -b "$DEVICE" ]] && { echo "Not a block device: $DEVICE" >&2; exit 1; }
[[ $EUID -ne 0    ]] && { echo "Must run as root (use sudo)" >&2; exit 1; }

# -------- safety --------
# Refuse to write to an NVMe device — those are essentially never
# removable USB media.
if [[ "$DEVICE" == /dev/nvme* ]]; then
    echo "REFUSING to write to an NVMe device ($DEVICE)." >&2
    exit 1
fi

# Warn (but don't refuse) if the kernel says this device isn't removable.
# /sys/block/<dev>/removable is "1" for USB sticks and "0" for internal
# disks — much more reliable than guessing from /dev/sdX names.
DEV_NAME=$(basename "$DEVICE")
removable=$(cat "/sys/block/$DEV_NAME/removable" 2>/dev/null || echo "?")
if [[ "$removable" != "1" ]]; then
    echo
    echo "!! WARNING: $DEVICE is NOT marked removable (flag = $removable)."
    echo "!! If this is genuinely your USB, proceed. If not — STOP NOW."
    echo
fi

# -------- confirm --------
echo
echo "=== About to DESTROY ALL DATA on $DEVICE ==="
echo
lsblk "$DEVICE" || true
echo
echo "ISO:    $ISO"
echo "Device: $DEVICE"
echo "Populate NOMAD_DATA: $POPULATE"
echo
read -r -p "Type 'yes' (exactly) to continue: " confirm
[[ "$confirm" != "yes" ]] && { echo "Aborted."; exit 0; }

# -------- unmount any existing partitions --------
echo ">>> Unmounting any existing partitions on $DEVICE"
for part in $(lsblk -ln -o PATH "$DEVICE" | tail -n +2); do
    umount "$part" 2>/dev/null || true
done

# -------- write ISO --------
echo ">>> Writing ISO — this will take a few minutes, be patient"
dd if="$ISO" of="$DEVICE" bs=4M status=progress conv=fsync
sync

# Tell the kernel to re-read the partition table the ISO wrote.
partprobe "$DEVICE"
sleep 2

# -------- extend GPT to end of device --------
# dd'ing the ISO wrote a GPT sized for the ISO, not the USB. The backup
# header sits way short of the actual end of the disk, and there's no
# free space for new partitions. `sgdisk --move-second-header` rewrites
# the backup GPT at the real end of the disk, which extends the usable
# area and lets us add partitions.
echo ">>> Extending GPT to the full device"
sgdisk --move-second-header "$DEVICE"

# -------- add NOMAD_DATA partition --------
# Find the highest existing partition number, then create the next one
# filling all remaining free space.
last_part=$(sgdisk --print "$DEVICE" | awk '/^[[:space:]]*[0-9]+/{n=$1} END{print n+0}')
next_part=$((last_part + 1))

echo ">>> Creating partition $next_part as NOMAD_DATA"
# 8300 = Linux filesystem type code
sgdisk \
    --new="${next_part}:0:0" \
    --typecode="${next_part}:8300" \
    --change-name="${next_part}:NOMAD_DATA" \
    "$DEVICE"

partprobe "$DEVICE"
sleep 2

# Resolve the actual partition device node. Regular /dev/sdX gives
# /dev/sdX1, /dev/sdX2. nvme-style /dev/nvme0n1 would give /dev/nvme0n1p1
# (we blocked nvme above, but keep the logic portable).
if [[ "$DEVICE" =~ [0-9]$ ]]; then
    DATA_PART="${DEVICE}p${next_part}"
else
    DATA_PART="${DEVICE}${next_part}"
fi

[[ ! -b "$DATA_PART" ]] && { echo "Expected partition $DATA_PART not found" >&2; exit 1; }

echo ">>> Formatting $DATA_PART as ext4 (label: NOMAD_DATA)"
mkfs.ext4 -F -L NOMAD_DATA "$DATA_PART"

# -------- mount + base layout --------
MOUNT_POINT=$(mktemp -d)
trap 'umount "$MOUNT_POINT" 2>/dev/null || true; rmdir "$MOUNT_POINT" 2>/dev/null || true' EXIT

mount "$DATA_PART" "$MOUNT_POINT"
mkdir -p "$MOUNT_POINT/docker" "$MOUNT_POINT/nomad" "$MOUNT_POINT/images"

# -------- optional populate --------
if $POPULATE; then
    echo
    echo ">>> Populating NOMAD_DATA (clone repo + pull images)"

    SCRIPT_DIR="$( cd "$(dirname "${BASH_SOURCE[0]}")" && pwd )"
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    TEMPLATE_COMPOSE="$PROJECT_ROOT/config/includes.chroot/usr/share/nomad/templates/docker-compose.yml"

    # Clone the Nomad repo for entrypoint.sh, wait-for-it.sh, sidecar-updater/
    REPO_TMP=$(mktemp -d)
    echo "    cloning Nomad repo..."
    git clone --depth 1 "$NOMAD_REPO" "$REPO_TMP/repo"

    # Copy everything from install/ into nomad/ on the partition
    echo "    copying install/ into /nomad/"
    cp -r "$REPO_TMP/repo/install/." "$MOUNT_POINT/nomad/"

    # Critical: .sh files in the repo are not executable in git, but
    # they need +x because compose bind-mounts them AS the container
    # entrypoint. Without this, the admin container hits:
    #   "exec: "/usr/local/bin/entrypoint.sh": permission denied"
    # and the whole nomad-start service fails.
    echo "    fixing script permissions"
    find "$MOUNT_POINT/nomad" -maxdepth 2 -name '*.sh' -exec chmod +x {} \;

    # Overwrite upstream compose.yml with our adapted template
    if [[ -f "$TEMPLATE_COMPOSE" ]]; then
        echo "    installing adapted docker-compose.yml"
        cp "$TEMPLATE_COMPOSE" "$MOUNT_POINT/nomad/docker-compose.yml"
    else
        echo "    WARNING: adapted compose template not found at:"
        echo "             $TEMPLATE_COMPOSE"
        echo "    Using upstream compose.yml — paths will be wrong!"
    fi

    rm -rf "$REPO_TMP"

    # Pull + save images. docker save bundles them into one tarball.
    echo "    pulling docker images (needs internet)..."
    docker pull ghcr.io/crosstalk-solutions/project-nomad:latest
    docker pull amir20/dozzle:v10.0
    docker pull mysql:8.0
    docker pull redis:7-alpine

    echo "    saving images to /images/nomad.tar (this takes a while)..."
    docker save \
        ghcr.io/crosstalk-solutions/project-nomad:latest \
        amir20/dozzle:v10.0 \
        mysql:8.0 \
        redis:7-alpine \
        -o "$MOUNT_POINT/images/nomad.tar"
fi

# -------- done --------
echo
echo ">>> Syncing and unmounting"
sync
umount "$MOUNT_POINT"
rmdir "$MOUNT_POINT"
trap - EXIT

echo
echo "=== DONE ==="
echo
lsblk "$DEVICE"
echo
echo "Next: boot a machine from $DEVICE and verify the nomad services"
echo "come up. SSH as nomad@<ip>, password nomad."
