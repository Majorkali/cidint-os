SUMMARY = "First program inside a CIDINT security domain"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

FILESEXTRAPATHS:prepend := "${CIDINT_REPO}/domains:"
SRC_URI = "file://domain-init"

S = "${WORKDIR}"

do_install() {
    install -m 0755 ${WORKDIR}/domain-init ${D}/init
    # The kernel opens /dev/console before /init runs.
    install -d ${D}/dev
    mknod -m 622 ${D}/dev/console c 5 1
}

FILES:${PN} = "/init /dev"
RDEPENDS:${PN} = "busybox"
