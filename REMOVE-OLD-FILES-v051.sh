#!/bin/bash
# Run after extracting this tarball. Removes the broken target/chooser
# units from v0.5.0 that caused the boot hang.
set -e
cd "$(dirname "$0")"
rm -fv config/includes.chroot/etc/systemd/system/nomad-firstboot.target
rm -fv config/includes.chroot/etc/systemd/system/nomad-firstboot-chooser.service
echo "✓ broken v0.5.0 units removed"
