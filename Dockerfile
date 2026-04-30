# Project Nomad USB — live-build environment
#
# Debian Trixie (13) is current stable. Using it for newer kernel =
# broader hardware compatibility, which is one of our stated goals.
# The ISO we BUILD with this will also be Trixie-based.

FROM debian:trixie

ENV DEBIAN_FRONTEND=noninteractive

# live-build itself, plus all the supporting tools it shells out to.
# If any of these are missing, live-build fails in confusing ways
# partway through a 20-minute build, so we install them all up front.
RUN apt-get update && apt-get install -y --no-install-recommends \
    live-build \
    debootstrap \
    squashfs-tools \
    xorriso \
    isolinux \
    syslinux-common \
    grub-pc-bin \
    grub-efi-amd64-bin \
    mtools \
    dosfstools \
    ca-certificates \
    curl \
    wget \
    git \
    sudo \
    rsync \
    procps \
    && rm -rf /var/lib/apt/lists/*

# All live-build work happens here. The host's ./config and ./output
# dirs get mounted in at runtime (see build.sh).
WORKDIR /build

CMD ["/bin/bash"]
