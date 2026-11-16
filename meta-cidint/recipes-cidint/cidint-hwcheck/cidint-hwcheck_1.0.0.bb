SUMMARY = "CIDINT OS hardware report and driver tools"
DESCRIPTION = "cidint-hwcheck records firmware, devices and their drivers, \
network, graphics, storage and service health of the computer it runs on, \
as evidence for real-hardware testing (reads only). cidint-drivers finds \
devices without a driver, loads available modules and adds firmware from \
a USB stick to the data volume. cidint-secureboot shows the Secure Boot \
state and exports the CIDINT certificate."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

FILESEXTRAPATHS:prepend := "${CIDINT_REPO}/hardware:"
SRC_URI = "file://cidint-hwcheck file://cidint-secureboot file://cidint-drivers file://cidint-firmware.service"

S = "${WORKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "cidint-firmware.service"

do_install() {
    install -d ${D}${sbindir} ${D}${systemd_system_unitdir}
    for p in cidint-hwcheck cidint-secureboot cidint-drivers; do
        install -m 0755 ${WORKDIR}/$p ${D}${sbindir}/$p
    done
    install -m 0644 ${WORKDIR}/cidint-firmware.service ${D}${systemd_system_unitdir}/
}

RDEPENDS:${PN} = "util-linux-dmesg openssl-bin kmod diffutils findutils"
