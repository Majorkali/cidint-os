#!/usr/bin/env bash
# Build the CIDINT OS live ISO.
#
#   sudo bash build.sh                 # amd64
#   sudo CIDINT_ARCH=arm64 bash build.sh   # on an arm64 machine
#
# Works on any Debian/Ubuntu system, including WSL2. If the host is not
# Debian 13 the script bootstraps a minimal Debian 13 build environment and
# re-runs itself inside it, so the result does not depend on the host.
# Needs root, ~25 GB free disk and a network connection. Takes 30-90 minutes.
set -euo pipefail

ARCH="${CIDINT_ARCH:-amd64}"
SRC="${CIDINT_SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
OUT="${CIDINT_OUT:-$SRC/out}"
WORK="${CIDINT_WORK:-/var/tmp/cidint-build}"
MIRROR="${CIDINT_MIRROR:-http://deb.debian.org/debian}"
MAX_BYTES=$((15 * 1024 * 1024 * 1024))
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export DEBIAN_FRONTEND=noninteractive

log() { printf '\n\033[1;36m[cidint]\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31m[cidint] %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root: sudo bash build.sh"

stage_source() {  # copy the tree to a Linux filesystem and normalise it
    local dest="$1"
    rm -rf "$dest"
    mkdir -p "$dest"
    tar -C "$SRC" --exclude=./out --exclude=./.git -cf - . | tar -C "$dest" -xf -
    find "$dest" -type f -exec sed -i 's/\r$//' {} +     # files edited on Windows
    find "$dest" -type d -exec chmod 755 {} +
    find "$dest" -type f -exec chmod 644 {} +
    chmod 755 "$dest/build.sh" "$dest/live/auto/config" \
              "$dest/live/config/includes.chroot/usr/bin/cidint"
    chmod 755 "$dest"/live/config/hooks/normal/*
}

inside() {  # the actual build; expects a Debian 13 environment
    log "installing build tools"
    apt-get update
    apt-get install -y live-build python3 ca-certificates
    cd "$1/live"
    python3 "$1/tools/patch-bootloaders.py" config
    log "configuring ($ARCH)"
    CIDINT_ARCH="$ARCH" lb config
    log "building image: this is the long part"
    lb build 2>&1 | tee "$1/build.log"
    local iso
    iso="$(ls -1 ./*.iso 2>/dev/null | head -n1 || true)"
    [ -n "$iso" ] || die "live-build finished without producing an ISO; see build.log"
    mv "$iso" "$1/cidint-os-$ARCH.iso"
}

if [ "${1:-}" = "--inside" ]; then
    inside "$2"
    exit 0
fi

. /etc/os-release
STAGE="$WORK/src"
if [ "${ID:-}" = "debian" ] && [ "${VERSION_CODENAME:-}" = "trixie" ] && [ -z "${CIDINT_FORCE_CHROOT:-}" ]; then
    log "host is Debian 13: building directly"
    stage_source "$STAGE"
    inside "$STAGE"
    RESULT="$STAGE"
else
    log "host is ${PRETTY_NAME:-unknown}: bootstrapping a Debian 13 build environment"
    apt-get update
    apt-get install -y debootstrap debian-archive-keyring
    BUILDER="$WORK/builder"
    if [ ! -x "$BUILDER/usr/bin/apt-get" ]; then
        rm -rf "$BUILDER"
        debootstrap --variant=minbase trixie "$BUILDER" "$MIRROR"
    fi
    cleanup() {
        for m in dev/pts dev sys proc; do umount -l "$BUILDER/$m" 2>/dev/null || true; done
    }
    trap cleanup EXIT
    cleanup
    mount -t proc proc "$BUILDER/proc"
    mount -t sysfs sys "$BUILDER/sys"
    mount --bind /dev "$BUILDER/dev"
    mount -t devpts devpts "$BUILDER/dev/pts"
    cp /etc/resolv.conf "$BUILDER/etc/resolv.conf" 2>/dev/null || true
    stage_source "$BUILDER/build"
    chroot "$BUILDER" /usr/bin/env -i PATH="$PATH" HOME=/root TERM="${TERM:-dumb}" \
        DEBIAN_FRONTEND=noninteractive CIDINT_ARCH="$ARCH" \
        /bin/bash /build/build.sh --inside /build
    RESULT="$BUILDER/build"
fi

ISO="$RESULT/cidint-os-$ARCH.iso"
[ -f "$ISO" ] || die "build failed; see $RESULT/build.log"
SIZE=$(stat -c %s "$ISO")
[ "$SIZE" -le "$MAX_BYTES" ] || die "ISO is $((SIZE / 1048576)) MB, over the 15 GB limit"
mkdir -p "$OUT"
cp "$ISO" "$OUT/"
cp "$RESULT/build.log" "$OUT/build-$ARCH.log" 2>/dev/null || true
( cd "$OUT" && sha256sum "cidint-os-$ARCH.iso" > "cidint-os-$ARCH.iso.sha256" )
log "done: $OUT/cidint-os-$ARCH.iso ($((SIZE / 1048576)) MB)"
log "write it to a USB stick with Rufus (DD mode), balenaEtcher or: dd if=... of=/dev/sdX bs=4M status=progress"
