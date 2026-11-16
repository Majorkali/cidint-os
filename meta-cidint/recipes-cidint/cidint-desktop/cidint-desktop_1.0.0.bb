SUMMARY = "CIDINT OS desktop: login screen, session, file manager, settings, assistant"
DESCRIPTION = "A Wayland desktop on Weston (software rendering). Installed \
systems start at a login screen; live media start the session directly. \
The programs (login screen, Files, Settings, Assistant) are GTK 3 programs \
written for CIDINT OS; privileged steps go through cidint-authd."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

FILESEXTRAPATHS:prepend := "${CIDINT_REPO}/desktop:"
SRC_URI = "\
    file://weston.ini \
    file://weston-greeter.ini \
    file://wallpaper.png \
    file://icons \
    file://seatd.service \
    file://cidint-desktop@.service \
    file://cidint-greeter.service \
    file://cidint-authd.service \
    file://cidint-authd \
    file://cidint-authctl \
    file://cidint-greeter \
    file://cidint-files \
    file://cidint-settings \
    file://cidint-assistant \
    file://cidint-session-setup \
    file://cidint-after-session \
    file://cidint_desktop/__init__.py \
    file://cidint_desktop/authclient.py \
    file://cidint_desktop/fileops.py \
    file://cidint_desktop/theme.py \
"

S = "${WORKDIR}"

inherit systemd useradd features_check

REQUIRED_DISTRO_FEATURES = "wayland"

SYSTEMD_SERVICE:${PN} = "seatd.service cidint-authd.service cidint-greeter.service"

USERADD_PACKAGES = "${PN}"
GROUPADD_PARAM:${PN} = "-r seat; -r render; -r cidint-desktop"
# cidint: the desktop user (password set at installation or first start).
# cidint-greeter: runs the login screen; no shell, no home.
USERADD_PARAM:${PN} = "\
    --uid 1000 --user-group --home-dir /home/cidint --no-create-home --shell /bin/sh \
        -G seat,video,input,render,cidint-desktop cidint; \
    --system --user-group --home-dir /nonexistent --no-create-home --shell /sbin/nologin \
        -G seat,video,input,render cidint-greeter \
"

LIBDIR = "${nonarch_libdir}/cidint/desktop"

do_install() {
    install -d ${D}${sysconfdir}/cidint ${D}${systemd_system_unitdir} ${D}${datadir}/cidint/icons \
        ${D}${LIBDIR}/cidint_desktop ${D}${bindir}
    install -m 0644 ${WORKDIR}/weston.ini ${D}${sysconfdir}/cidint/weston.ini
    install -m 0644 ${WORKDIR}/weston-greeter.ini ${D}${sysconfdir}/cidint/weston-greeter.ini
    install -m 0644 ${WORKDIR}/wallpaper.png ${D}${datadir}/cidint/wallpaper.png
    install -m 0644 ${WORKDIR}/icons/*.png ${D}${datadir}/cidint/icons/
    for u in seatd.service cidint-desktop@.service cidint-greeter.service cidint-authd.service; do
        install -m 0644 ${WORKDIR}/$u ${D}${systemd_system_unitdir}/
    done
    for p in cidint-authd cidint-authctl cidint-greeter cidint-files cidint-settings cidint-assistant \
             cidint-session-setup cidint-after-session; do
        install -m 0755 ${WORKDIR}/$p ${D}${LIBDIR}/$p
    done
    install -m 0644 ${WORKDIR}/cidint_desktop/*.py ${D}${LIBDIR}/cidint_desktop/
    for p in cidint-authctl cidint-greeter cidint-files cidint-settings cidint-assistant; do
        ln -sf ${LIBDIR}/$p ${D}${bindir}/$p
    done
}

FILES:${PN} += "${datadir}/cidint ${LIBDIR} ${systemd_system_unitdir}"

RDEPENDS:${PN} = "\
    weston seatd liberation-fonts xkeyboard-config cidint-storage \
    python3-core python3-modules python3-pygobject gtk+3 \
    adwaita-icon-theme adwaita-icon-theme-symbolic hicolor-icon-theme \
    gdk-pixbuf-loader-png libxcrypt openssl-bin \
"
