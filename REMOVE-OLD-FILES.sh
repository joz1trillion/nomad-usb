#!/bin/bash
# Run after extracting the v1 frontend tarball into your repo.
# Removes files that are no longer referenced after the wizard rewrite.
#
# After running this, do:
#   git status
#   git add -A
#   git commit -m "v0.6.0: rewrite flash tool wizard for prebuilt-docker workflow"
#   git push
set -e
cd "$(dirname "$0")"

echo "Removing files no longer needed in v0.6.0..."

# browse.py — was used by the in-app file picker (zenity + filesystem
# browser). v1 doesn't pick ISOs from disk by default; the local-ISO
# override accepts a path you type in. So no picker UI, no module.
rm -fv flash/nomad_flash/browse.py

# apps_catalog.py — drove the optional-apps wizard step. v1 is
# all-or-nothing (full or base mode) so the catalog isn't queried
# anywhere. Keep the file off the install rather than carrying dead
# code that could mislead future readers.
rm -fv flash/nomad_flash/apps_catalog.py

# verify.py / verify-template subcommand — was a power-user tool to
# diff our compose template against an ISO's. The new pipeline always
# uses the bundled compose, so there's nothing to verify against in
# the same way. Worth bringing back later if the gap matters.
rm -fv flash/nomad_flash/verify.py

echo ""
echo "✓ obsolete files removed"
echo ""
echo "Next:"
echo "  sudo pip install --break-system-packages -e flash"
echo "  sudo nomad-flash    # test the new wizard"
