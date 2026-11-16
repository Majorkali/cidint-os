SUMMARY = "CIDINT OS root account policy"
DESCRIPTION = "Images ship with root locked. On the first start of an \
installed system the console asks for a root password before offering a \
login; live media log in automatically on the local console."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

FILESEXTRAPATHS:prepend := "${CIDINT_REPO}/accounts:"
SRC_URI = "\
    file://cidint-root-password \
    file://cidint-live-autologin \
"

S = "${WORKDIR}"

do_install() {
    install -d ${D}${sbindir} ${D}${systemd_unitdir}/system-generators
    install -m 0755 ${WORKDIR}/cidint-root-password ${D}${sbindir}/cidint-root-password
    install -m 0755 ${WORKDIR}/cidint-live-autologin ${D}${systemd_unitdir}/system-generators/cidint-live-autologin
    # Before each console login prompt: ask for the root password if none
    # is set yet (no-op on live media and once a password exists).
    for unit in getty@ serial-getty@; do
        install -d ${D}${systemd_system_unitdir}/${unit}.service.d
        printf '%s\n' '[Service]' \
            "ExecStartPre=/bin/sh -c 'exec ${sbindir}/cidint-root-password --first-start </dev/%I >/dev/%I 2>&1'" \
            > ${D}${systemd_system_unitdir}/${unit}.service.d/cidint-root-password.conf
    done
}

FILES:${PN} += "\
    ${systemd_unitdir}/system-generators \
    ${systemd_system_unitdir}/getty@.service.d \
    ${systemd_system_unitdir}/serial-getty@.service.d \
"

RDEPENDS:${PN} = "openssl-bin util-linux-agetty"
