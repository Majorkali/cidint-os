SUMMARY = "CIDINT OS desktop: login screen, session and desktop programs"
DESCRIPTION = "A Wayland desktop on Weston (OpenGL through Mesa where the \
graphics hardware has a driver, software rendering otherwise). Installed \
systems start at a login screen; live media start the session directly. \
Programs written for CIDINT OS: login screen, Files, Settings (with Wi-Fi, \
sound and display), Assistant, Media, Browser (WebKitGTK) and Software \
(signed app store). Privileged steps go through cidint-authd."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

FILESEXTRAPATHS:prepend := "${CIDINT_REPO}/desktop:"
PROGRAMS = "cidint-authd cidint-authctl cidint-greeter cidint-files cidint-settings cidint-assistant \
            cidint-media cidint-browser cidint-software cidint-store cidint-wifi \
            cidint-session-setup cidint-after-session"
MODULES = "__init__ authclient fileops theme wifi store settings_hw"
SRC_URI = "\
    file://weston.ini \
    file://weston-greeter.ini \
    file://wallpaper.png \
    file://icons \
    file://sounds \
    file://seatd.service \
    file://cidint-desktop@.service \
    file://cidint-greeter.service \
    file://cidint-authd.service \
    file://browser/start.html \
    ${@' '.join('file://' + p for p in d.getVar('PROGRAMS').split())} \
    ${@' '.join('file://cidint_desktop/' + m + '.py' for m in d.getVar('MODULES').split())} \
"

S = "${WORKDIR}"

inherit systemd useradd features_check

REQUIRED_DISTRO_FEATURES = "wayland"

SYSTEMD_SERVICE:${PN} = "seatd.service cidint-authd.service cidint-greeter.service"

# Trusted key of the software store (same scheme as update keys: only the
# public key, handed in by the build as the base64 line of a PEM file).
CIDINT_APPS_PUBKEY ??= ""

USERADD_PACKAGES = "${PN}"
GROUPADD_PARAM:${PN} = "-r seat; -r render; -r cidint-desktop; -r netdev"
# cidint: the desktop user (password set at installation or first start).
# cidint-greeter: runs the login screen; no shell, no home.
USERADD_PARAM:${PN} = "\
    --uid 1000 --user-group --home-dir /home/cidint --no-create-home --shell /bin/sh \
        -G seat,video,input,render,audio,netdev,cidint-desktop cidint; \
    --system --user-group --home-dir /nonexistent --no-create-home --shell /sbin/nologin \
        -G seat,video,input,render cidint-greeter \
"

LIBDIR = "${nonarch_libdir}/cidint/desktop"

do_install() {
    install -d ${D}${sysconfdir}/cidint ${D}${systemd_system_unitdir} ${D}${datadir}/cidint/icons \
        ${D}${datadir}/cidint/sounds ${D}${datadir}/cidint/browser ${D}${LIBDIR}/cidint_desktop ${D}${bindir}
    install -m 0644 ${WORKDIR}/weston.ini ${D}${sysconfdir}/cidint/weston.ini
    install -m 0644 ${WORKDIR}/weston-greeter.ini ${D}${sysconfdir}/cidint/weston-greeter.ini
    install -m 0644 ${WORKDIR}/wallpaper.png ${D}${datadir}/cidint/wallpaper.png
    install -m 0644 ${WORKDIR}/icons/*.png ${D}${datadir}/cidint/icons/
    install -m 0644 ${WORKDIR}/sounds/*.wav ${D}${datadir}/cidint/sounds/
    install -m 0644 ${WORKDIR}/browser/start.html ${D}${datadir}/cidint/browser/start.html
    for u in seatd.service cidint-desktop@.service cidint-greeter.service cidint-authd.service; do
        install -m 0644 ${WORKDIR}/$u ${D}${systemd_system_unitdir}/
    done
    for p in ${PROGRAMS}; do
        install -m 0755 ${WORKDIR}/$p ${D}${LIBDIR}/$p
    done
    install -m 0644 ${WORKDIR}/cidint_desktop/*.py ${D}${LIBDIR}/cidint_desktop/
    for p in cidint-authctl cidint-greeter cidint-files cidint-settings cidint-assistant cidint-media \
             cidint-browser cidint-software cidint-store cidint-wifi; do
        ln -sf ${LIBDIR}/$p ${D}${bindir}/$p
    done

    # Software store: trusted key; installed apps live on the data volume.
    install -d ${D}${datadir}/cidint/keys/apps
    if [ -n "${CIDINT_APPS_PUBKEY}" ]; then
        printf '%s\n' '-----BEGIN PUBLIC KEY-----' '${CIDINT_APPS_PUBKEY}' '-----END PUBLIC KEY-----' \
            > ${D}${datadir}/cidint/keys/apps/build.pem
    else
        bbwarn "CIDINT_APPS_PUBKEY is not set: the software store will refuse every catalogue"
    fi
    install -d ${D}${nonarch_libdir}/tmpfiles.d
    printf '%s\n' 'd /data/apps/store 0755 root root -' 'd /data/config/iwd 0700 root root -' \
        > ${D}${nonarch_libdir}/tmpfiles.d/cidint-desktop.conf

    # Wi-Fi (iwd): addresses by DHCP from iwd itself, names through
    # systemd-resolved; known networks kept on the data volume.
    install -d ${D}${sysconfdir}/iwd ${D}${localstatedir}/lib/iwd ${D}${systemd_system_unitdir}/iwd.service.d
    printf '%s\n' '[General]' 'EnableNetworkConfiguration=true' '' '[Network]' 'NameResolvingService=systemd' \
        > ${D}${sysconfdir}/iwd/main.conf
    printf '%s\n' '[Unit]' 'RequiresMountsFor=/data' 'After=systemd-tmpfiles-setup.service' '' \
        '[Service]' 'BindPaths=/data/config/iwd:/var/lib/iwd' \
        > ${D}${systemd_system_unitdir}/iwd.service.d/cidint.conf
}

FILES:${PN} += "${datadir}/cidint ${LIBDIR} ${systemd_system_unitdir} ${nonarch_libdir}/tmpfiles.d \
                ${localstatedir}/lib/iwd"

RDEPENDS:${PN} = "\
    weston seatd liberation-fonts xkeyboard-config cidint-storage \
    python3-core python3-modules python3-pygobject gtk+3 gtk4 \
    adwaita-icon-theme adwaita-icon-theme-symbolic hicolor-icon-theme \
    gdk-pixbuf-loader-png gdk-pixbuf-loader-jpeg gdk-pixbuf-loader-gif libxcrypt openssl-bin \
    webkitgtk glib-networking ca-certificates \
    gstreamer1.0 gstreamer1.0-plugins-base-meta gstreamer1.0-plugins-good-meta \
    alsa-utils-amixer alsa-utils-aplay \
    iwd wireless-regdb-static \
    mesa-megadriver \
"
