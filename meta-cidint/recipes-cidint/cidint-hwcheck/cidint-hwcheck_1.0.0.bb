SUMMARY = "CIDINT OS hardware report"
DESCRIPTION = "cidint-hwcheck records firmware, devices and their drivers, \
network, graphics, storage and service health of the computer it runs on, \
as evidence for real-hardware testing. Reads only."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

FILESEXTRAPATHS:prepend := "${CIDINT_REPO}/hardware:"
SRC_URI = "file://cidint-hwcheck"

S = "${WORKDIR}"

do_install() {
    install -d ${D}${sbindir}
    install -m 0755 ${WORKDIR}/cidint-hwcheck ${D}${sbindir}/cidint-hwcheck
}

RDEPENDS:${PN} = "util-linux-dmesg"
