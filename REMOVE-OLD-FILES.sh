#!/bin/bash
# Run this AFTER extracting the tarball into your repo. Removes
# files renamed in v0.5.0 so we don't ship two copies in the next
# ISO build.
set -e
cd "$(dirname "$0")"
rm -fv config/includes.chroot/etc/systemd/system/nomad-firstboot.service
rm -fv config/includes.chroot/usr/local/sbin/nomad-firstboot
echo "✓ old files removed"
