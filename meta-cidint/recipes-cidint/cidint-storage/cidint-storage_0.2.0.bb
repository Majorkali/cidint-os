SUMMARY = "CIDINT persistent storage layout"
DESCRIPTION = "Creates the directory layout on the /data volume and binds \
/home to it, so user data lives outside the read-only system image."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

SRC_URI = "\
    file://cidint-data-setup.service \
    file://home.mount \
"

S = "${WORKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "cidint-data-setup.service home.mount"

do_install() {
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/cidint-data-setup.service ${D}${systemd_system_unitdir}/
    install -m 0644 ${WORKDIR}/home.mount ${D}${systemd_system_unitdir}/
    # Mount point for the data volume (the wic layout adds the fstab entry).
    install -d ${D}/data
}

FILES:${PN} += "/data"
