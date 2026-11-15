SUMMARY = "CIDINT OS graphical session"
DESCRIPTION = "A Wayland session (Weston desktop shell, software rendering) \
that starts at boot as an unprivileged user. This is a basic graphical \
session, not the planned NOVA desktop; there is no login screen yet."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

SRC_URI = "\
    file://weston.ini \
    file://seatd.service \
    file://cidint-desktop.service \
"

S = "${WORKDIR}"

inherit systemd useradd features_check

REQUIRED_DISTRO_FEATURES = "wayland"

SYSTEMD_SERVICE:${PN} = "seatd.service cidint-desktop.service"

USERADD_PACKAGES = "${PN}"
GROUPADD_PARAM:${PN} = "-r seat; -r render"
USERADD_PARAM:${PN} = "--uid 1000 --user-group --home-dir /home/cidint --no-create-home --shell /bin/sh -G seat,video,input,render cidint"

do_install() {
    install -d ${D}${sysconfdir}/cidint ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/weston.ini ${D}${sysconfdir}/cidint/weston.ini
    install -m 0644 ${WORKDIR}/seatd.service ${D}${systemd_system_unitdir}/
    install -m 0644 ${WORKDIR}/cidint-desktop.service ${D}${systemd_system_unitdir}/
}

RDEPENDS:${PN} = "weston seatd liberation-fonts xkeyboard-config cidint-storage"
