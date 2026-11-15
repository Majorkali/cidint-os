SUMMARY = "CIDINT OS host firewall"
DESCRIPTION = "nftables rule set loaded before the network comes up: inbound \
and forwarded traffic is dropped by default, outbound traffic is allowed."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

SRC_URI = "\
    file://firewall.nft \
    file://cidint-firewall.service \
"

S = "${WORKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "cidint-firewall.service"

do_install() {
    install -d ${D}${sysconfdir}/cidint ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/firewall.nft ${D}${sysconfdir}/cidint/firewall.nft
    install -m 0644 ${WORKDIR}/cidint-firewall.service ${D}${systemd_system_unitdir}/
}

RDEPENDS:${PN} = "nftables"
