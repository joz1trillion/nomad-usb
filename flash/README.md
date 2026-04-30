# nomad-flash

Flash tool for the [Nomad USB](../) — writes a Nomad ISO to a USB drive,
creates a `NOMAD_DATA` partition, and pre-populates the data partition with
Project Nomad's container images so the resulting USB is fully offline-ready.

## How it works

1. Wipes the target USB
2. Creates a 3-partition GPT layout:
   - **EFI**         (512 MB, FAT32) — bootloader
   - **NOMAD_LIVE**  (6 GB, ext4)    — live ISO contents (squashfs)
   - **NOMAD_DATA**  (rest, ext4)    — docker root + Nomad data
3. Copies the contents of the ISO onto NOMAD_LIVE and installs GRUB
4. Pulls Project Nomad's Docker images on the host machine
5. Saves them to `/images/nomad.tar` on NOMAD_DATA
6. Writes the adapted `docker-compose.yml` to `/nomad/`

The end result is a USB that boots straight to a working Nomad install
with **no internet required**.

## Usage

```sh
# Web UI (default)
nomad-flash

# CLI mode — fully scripted, no browser
nomad-flash cli --iso PATH --device /dev/sdX

# Verify the bundled compose template matches an ISO's
nomad-flash verify-template --iso PATH
```

The web UI starts on `http://127.0.0.1:5050` and opens your browser.

## Requirements

- Linux (Windows support TBD)
- Python 3.10+
- Docker installed (for pulling images)
- Standard partition tools: `sgdisk`, `mkfs.ext4`, `mkfs.vfat`, `partprobe`
- root (for writing to block devices)

## Status

Early development. See `../README.md` for the project overview.
