# Project Nomad USB

A bootable, fully-offline Linux USB that runs [Project Nomad](https://github.com/Crosstalk-Solutions/project-nomad) — designed as a post-apocalypse survival tool.

## Architecture

- **Base:** Debian Trixie (13), minimal, headless
- **Boot:** Live ISO loads entirely into RAM (`toram`), USB speed irrelevant after boot
- **Storage:** Nomad data lives on a separate ext4 partition, labeled `NOMAD_DATA`
- **Docker:** `data-root` points directly at the NOMAD_DATA partition — no loop `.img` file
- **Network:** LAN by default, with hostapd/dnsmasq installed for optional hotspot mode

## Partition layout (on the target USB)

| Partition | Size    | Format | Purpose                          |
|-----------|---------|--------|----------------------------------|
| sda1      | 512 MB  | FAT32  | EFI + BIOS boot                  |
| sda2      | ~6 GB   | ext4   | Live ISO (squashfs, read-only)   |
| sda3      | rest    | ext4   | `NOMAD_DATA` (docker + config)   |

## Repo layout

```
nomad-usb/
├── Dockerfile         # Debian-based live-build environment
├── build.sh           # Builds the image, drops you into a shell
├── config/            # live-build config (populated in step 2)
└── output/            # Built ISOs land here
```

## Prerequisites

- Docker installed on the host
- Host OS doesn't matter (any Linux works; Arch/CachyOS tested)

## Usage (so far)

```sh
./build.sh
```

On first run, builds the `nomad-usb-builder` Docker image (~5 min).
On subsequent runs, just drops you into a shell in `/build`.

Currently this only gives you the build environment. The actual ISO build config comes in step 2.

## Status

- [x] Step 1: Build environment (Dockerfile + helper)
- [ ] Step 2: live-build config (hooks, package lists, systemd services)
- [ ] Step 3: `nomad-init` + `nomad-start` boot services
- [ ] Step 4: Adapted `docker-compose.yml`
- [ ] Step 5: First ISO build + test
- [ ] Step 6: Flash tool
