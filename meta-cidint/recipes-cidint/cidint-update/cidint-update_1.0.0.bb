SUMMARY = "CIDINT OS A/B system updates"
DESCRIPTION = "cidint-slot writes an update to the spare system slot, has it \
tried once, commits it when it comes up healthy and otherwise leaves the \
previous system in place. An update is accepted only with a valid Ed25519 \
signature from a trusted key and matching checksums."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

FILESEXTRAPATHS:prepend := "${CIDINT_REPO}/update:"
SRC_URI = "\
    file://cidint-slot \
    file://cidint-slot-confirm.service \
    file://cidint-slot-confirm.timer \
"

S = "${WORKDIR}"

# Trusted update-signing public key: the base64 line of an Ed25519 public
# key in PEM form, handed in by the build (kas passes the environment
# variable CIDINT_UPDATE_PUBKEY through). Only the PUBLIC key enters the
# image; the private key never belongs in this repository or the build.
# Without a key the image refuses every update.
CIDINT_UPDATE_PUBKEY ??= ""
CIDINT_UPDATE_KEY_NAME ??= "build.pem"

inherit systemd

SYSTEMD_SERVICE:${PN} = "cidint-slot-confirm.timer"

do_install() {
    install -d ${D}${sbindir} ${D}${systemd_system_unitdir}
    install -m 0755 ${WORKDIR}/cidint-slot ${D}${sbindir}/cidint-slot
    install -m 0644 ${WORKDIR}/cidint-slot-confirm.service ${D}${systemd_system_unitdir}/
    install -m 0644 ${WORKDIR}/cidint-slot-confirm.timer ${D}${systemd_system_unitdir}/
    install -d ${D}${datadir}/cidint/keys/update
    if [ -n "${CIDINT_UPDATE_PUBKEY}" ]; then
        printf '%s\n' '-----BEGIN PUBLIC KEY-----' '${CIDINT_UPDATE_PUBKEY}' '-----END PUBLIC KEY-----' \
            > ${D}${datadir}/cidint/keys/update/${CIDINT_UPDATE_KEY_NAME}
        chmod 0644 ${D}${datadir}/cidint/keys/update/${CIDINT_UPDATE_KEY_NAME}
    else
        bbwarn "CIDINT_UPDATE_PUBKEY is not set: this image will refuse every update"
    fi
}

FILES:${PN} += "${systemd_system_unitdir}/cidint-slot-confirm.service ${datadir}/cidint/keys"

RDEPENDS:${PN} = "grub-editenv e2fsprogs-e2fsck e2fsprogs-resize2fs openssl-bin"
