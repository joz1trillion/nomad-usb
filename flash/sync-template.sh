#!/usr/bin/env bash
#
# sync-template.sh — copy the canonical compose template into the
# flash tool's bundled directory.
#
# The canonical location is the ISO build's includes.chroot, so when
# the ISO is built and shipped it carries the same file. This script
# keeps the flash tool's bundled copy in sync.
#
# Run this any time the canonical compose template changes, before
# building/publishing the flash tool.

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

CANONICAL="$REPO_ROOT/config/includes.chroot/usr/share/nomad/templates/docker-compose.yml"
BUNDLED="$SCRIPT_DIR/nomad_flash/bundled/docker-compose.yml"

if [ ! -f "$CANONICAL" ]; then
    echo "Canonical template not found at: $CANONICAL" >&2
    exit 1
fi

cp "$CANONICAL" "$BUNDLED"
echo "Synced $CANONICAL -> $BUNDLED"
