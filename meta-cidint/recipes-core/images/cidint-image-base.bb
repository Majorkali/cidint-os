SUMMARY = "CIDINT OS base image: read-only system, separate persistent data volume"
DESCRIPTION = "Minimal bootable CIDINT OS for x86_64 UEFI. Console only; the \
desktop, security framework and editions build on this foundation."
LICENSE = "MIT"

inherit core-image

IMAGE_FEATURES += "read-only-rootfs"

IMAGE_INSTALL = "\
    packagegroup-core-boot \
    e2fsprogs-e2fsck \
    cidint-cli \
    cidint-storage \
    ${CORE_IMAGE_EXTRA_INSTALL} \
"

IMAGE_FSTYPES = "wic wic.bmap"
WKS_FILE = "cidint-ab.wks.in"
WKS_FILE_DEPENDS:append = " e2fsprogs-native"
IMAGE_ROOTFS_EXTRA_SPACE = "0"
