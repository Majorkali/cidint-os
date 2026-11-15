SUMMARY = "CIDINT persistent storage layout"
DESCRIPTION = "Creates the directory layout on the /data volume and binds \
/home to it, so user data lives outside the read-only system image."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

SRC_URI = "\
    file://cidint-data-setup.service \
    file://home.mount \
    file://cidint-live-data.service \
    file://cidint-data-unlock.service \
    file://cidint-data-unlock \
"

S = "${WORKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "cidint-data-setup.service home.mount cidint-live-data.service cidint-data-unlock.service"

do_install() {
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/cidint-data-setup.service ${D}${systemd_system_unitdir}/
    install -m 0644 ${WORKDIR}/home.mount ${D}${systemd_system_unitdir}/
    install -m 0644 ${WORKDIR}/cidint-live-data.service ${D}${systemd_system_unitdir}/
    install -m 0644 ${WORKDIR}/cidint-data-unlock.service ${D}${systemd_system_unitdir}/
    install -d ${D}${sbindir}
    install -m 0755 ${WORKDIR}/cidint-data-unlock ${D}${sbindir}/cidint-data-unlock
    # Mount point for the data volume (the wic layout adds the fstab entry).
    install -d ${D}/data
}

FILES:${PN} += "/data"

RDEPENDS:${PN} += "cryptsetup e2fsprogs-e2fsck"
