#!/usr/bin/env bash
#
# Helper script to build the Docker image and drop you into a shell
# inside the live-build environment.
#
# Usage:
#   ./build.sh            # interactive shell in the builder
#   ./build.sh --rebuild  # force rebuild of the Docker image

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
IMAGE_NAME="nomad-usb-builder"

# Sync the canonical VERSION file into the chroot so it ends up at
# /etc/nomad-version on the booted system. We do this every build
# (rather than committing the copy) so VERSION at the repo root stays
# the single source of truth — no chance of mismatch between what's
# in git and what the booted USB reports.
if [ -f "$SCRIPT_DIR/VERSION" ]; then
    VERSION=$(cat "$SCRIPT_DIR/VERSION" | tr -d '[:space:]')
    echo ">>> Building Project Nomad USB v${VERSION}"
    install -D -m 644 "$SCRIPT_DIR/VERSION" \
        "$SCRIPT_DIR/config/includes.chroot/etc/nomad-version"
else
    echo ">>> WARNING: no VERSION file at repo root — booted system will say 'unknown'" >&2
fi

# --rebuild forces a fresh image. Otherwise we reuse the existing one
# so we're not waiting on apt every time we want a shell.
if [[ "${1:-}" == "--rebuild" ]] || ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    echo ">>> Building Docker image: $IMAGE_NAME"
    docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"
fi

echo ">>> Entering builder shell."
echo ">>> Run 'lb config && lb build' from /build (NOT from /build/config)"
echo ">>> ISOs land at /build/live-image-amd64.hybrid.iso, copy to output/"
echo

# --privileged is required: live-build does chroot, loop mounts, and
# mknod, none of which work in a default container.
#
# auto/ and config/ are bind-mounted SEPARATELY because live-build
# expects them as SIBLINGS at the project root (/build). If they
# were nested, lb wouldn't find the customizations.
docker run --rm -it \
    --privileged \
    -v "$SCRIPT_DIR/auto:/build/auto" \
    -v "$SCRIPT_DIR/config:/build/config" \
    -v "$SCRIPT_DIR/output:/build/output" \
    "$IMAGE_NAME"
