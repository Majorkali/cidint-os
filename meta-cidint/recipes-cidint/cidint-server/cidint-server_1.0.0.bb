SUMMARY = "CIDINT OS Server Edition: remote administration over SSH"
DESCRIPTION = "OpenSSH daemon configuration with key-only logins and a host \
key kept on the data volume, plus the firewall rule that opens the SSH port."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

SRC_URI = "\
    file://sshd_config_cidint \
    file://10-ssh.nft \
    file://data-volume.conf \
"

S = "${WORKDIR}"

do_install() {
    install -d ${D}${sysconfdir}/ssh ${D}${sysconfdir}/cidint/firewall.d
    install -m 0644 ${WORKDIR}/sshd_config_cidint ${D}${sysconfdir}/ssh/sshd_config_cidint
    install -m 0644 ${WORKDIR}/10-ssh.nft ${D}${sysconfdir}/cidint/firewall.d/10-ssh.nft
    install -d ${D}${systemd_system_unitdir}/sshdgenkeys.service.d
    install -m 0644 ${WORKDIR}/data-volume.conf ${D}${systemd_system_unitdir}/sshdgenkeys.service.d/data-volume.conf
}

FILES:${PN} += "${systemd_system_unitdir}/sshdgenkeys.service.d"

RDEPENDS:${PN} = "openssh-sshd openssh-keygen cidint-firewall cidint-storage"
