SUMMARY = "CIDINT OS A/B system updates"
DESCRIPTION = "cidint-slot writes an update to the spare system slot, has it \
tried once, commits it when it comes up healthy and otherwise leaves the \
previous system in place. Updates are checked by checksum only; signature \
verification is not implemented."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

FILESEXTRAPATHS:prepend := "${CIDINT_REPO}/update:"
SRC_URI = "\
    file://cidint-slot \
    file://cidint-slot-confirm.service \
    file://cidint-slot-confirm.timer \
"

S = "${WORKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "cidint-slot-confirm.timer"

do_install() {
    install -d ${D}${sbindir} ${D}${systemd_system_unitdir}
    install -m 0755 ${WORKDIR}/cidint-slot ${D}${sbindir}/cidint-slot
    install -m 0644 ${WORKDIR}/cidint-slot-confirm.service ${D}${systemd_system_unitdir}/
    install -m 0644 ${WORKDIR}/cidint-slot-confirm.timer ${D}${systemd_system_unitdir}/
}

FILES:${PN} += "${systemd_system_unitdir}/cidint-slot-confirm.service"

RDEPENDS:${PN} = "grub-editenv e2fsprogs-e2fsck e2fsprogs-resize2fs"
