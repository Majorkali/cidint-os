SUMMARY = "CIDINT OS installer (console)"
DESCRIPTION = "Installs CIDINT OS from the live medium onto a disk with the \
CIDINT layout: ESP, two system slots and a separate data volume. Erases \
the target disk only after explicit confirmation."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

FILESEXTRAPATHS:prepend := "${CIDINT_REPO}/installer:"
SRC_URI = "file://cidint-install"

S = "${WORKDIR}"

do_install() {
    install -d ${D}${sbindir}
    install -m 0755 ${WORKDIR}/cidint-install ${D}${sbindir}/cidint-install
}

RDEPENDS:${PN} = "\
    util-linux-sfdisk \
    util-linux-blkid \
    util-linux-mount \
    util-linux-umount \
    dosfstools \
    e2fsprogs-mke2fs \
    e2fsprogs-e2fsck \
    e2fsprogs-resize2fs \
    udev \
"
