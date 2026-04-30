#!/bin/bash
# Run after extracting the v0.5.4 tarball into your repo. Removes
# the firstboot UI mode files that are now obsolete.
#
# After running this, do `git status` to see what changed, then
# `git add -A` and `git commit -m "v0.5.4: revert firstboot UI mode"`
# to record the change.
set -e
cd "$(dirname "$0")"

echo "Removing firstboot UI files..."
rm -fv config/includes.chroot/etc/systemd/system/nomad-firstboot-ui.service
rm -fv config/includes.chroot/usr/local/sbin/nomad-firstboot-ui
rm -rfv config/includes.chroot/etc/systemd/system/getty@tty1.service.d/

# Belt-and-suspenders: in case any earlier broken units survived
# (we removed these in v0.5.1 and v0.5.2 but better safe)
rm -fv config/includes.chroot/etc/systemd/system/nomad-firstboot.target
rm -fv config/includes.chroot/etc/systemd/system/nomad-firstboot-chooser.service

echo "✓ cleanup complete"
echo ""
echo "Next: review with 'git status', then commit with:"
echo "  git add -A"
echo "  git commit -m 'v0.5.4: revert firstboot UI mode and noisy-boot suppression'"
