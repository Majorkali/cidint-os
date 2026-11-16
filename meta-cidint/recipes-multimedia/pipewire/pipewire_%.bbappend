# CIDINT OS: PipeWire becomes the ALSA default only inside a desktop session
# (the session user's ~/.asoundrc, written by cidint-session-setup). System
# services and root on the console keep using the sound card directly.
do_install:append() {
    rm -f ${D}${sysconfdir}/alsa/conf.d/99-pipewire-default.conf
}
