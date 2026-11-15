SUMMARY = "System image of a CIDINT security domain (initramfs)"
DESCRIPTION = "A few megabytes: BusyBox and the domain's init program. It is \
loaded into memory by the machine emulator; a domain has no disk."
LICENSE = "MIT"

PACKAGE_INSTALL = "base-files base-passwd busybox cidint-domain-init"

IMAGE_FEATURES = ""
IMAGE_LINGUAS = ""
PACKAGE_EXCLUDE = "kernel-image-*"
IMAGE_NAME_SUFFIX = ""
IMAGE_FSTYPES = "cpio.gz"
IMAGE_ROOTFS_SIZE = "8192"
IMAGE_ROOTFS_EXTRA_SPACE = "0"

inherit core-image
