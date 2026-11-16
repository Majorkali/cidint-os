SUMMARY = "CIDINT OS boot files: GRUB that checks signatures, signed kernel and boot menu"
DESCRIPTION = "Builds the UEFI boot loader of installed systems and disk images. \
With a boot key (CIDINT_SB_KEY_DIR), GRUB is signed for UEFI Secure Boot \
(Authenticode, sbsign) and carries the public half of a GPG boot key; it then \
refuses a kernel or boot menu without a valid detached signature \
(check_signatures=enforce). The firmware checks GRUB, GRUB checks the rest. \
The same files are installed under /usr/share/cidint/boot so that the \
installer and system updates use exactly what was signed."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

inherit deploy

COMPATIBLE_HOST = "x86_64.*-linux"
PACKAGE_ARCH = "${MACHINE_ARCH}"
DEPENDS = "grub-native grub-efi"
do_compile[depends] += "virtual/kernel:do_deploy"

# Directory holding the boot keys of this build (never part of the
# repository): db.key/db.crt/db.cer (UEFI signing key and certificate),
# boot.gpg (public GPG boot key, binary) and gnupg/ (GPG home with its
# private half). CIDINT_SB_KEY_ID names the keys so that a new key causes
# new signatures instead of reusing cached ones.
CIDINT_SB_KEY_DIR ??= ""
CIDINT_SB_KEY_ID ??= ""
do_compile[vardepsexclude] += "CIDINT_SB_KEY_DIR"
do_compile[vardeps] += "CIDINT_SB_KEY_ID"

# Everything GRUB needs is built in: with Secure Boot on, GRUB is locked
# down and loads no modules from disk.
CIDINT_GRUB_MODULES = "boot linux ext2 fat serial terminal part_gpt part_msdos normal efi_gop \
    configfile loadenv test echo regexp probe pgp gcry_rsa gcry_sha256 gcry_sha512 \
    reboot halt sleep"

# Kernel arguments of installed systems (the machine's consoles come from APPEND).
CIDINT_BOOT_ARGS = "rootwait rootfstype=ext4 ro cidint.mode=installed ${APPEND}"

S = "${WORKDIR}"
B = "${WORKDIR}/build"

do_configure[noexec] = "1"

do_compile() {
    rm -rf ${B}/out
    install -d ${B}/out
    MODDIR=${STAGING_LIBDIR}/grub/${GRUB_TARGET_DIR}
    [ -f "$MODDIR/pgp.mod" ] || bbfatal "GRUB EFI modules not found in $MODDIR"
    KEYS="${CIDINT_SB_KEY_DIR}"

    if [ -n "$KEYS" ]; then
        for f in db.key db.crt db.cer boot.gpg gnupg; do
            [ -e "$KEYS/$f" ] || bbfatal "CIDINT_SB_KEY_DIR ($KEYS) lacks $f"
        done
        printf '%s\n' '# Built in: every file GRUB loads needs a valid signature.' \
            'set check_signatures=enforce' > ${B}/early.cfg
        PUBKEY="--pubkey $KEYS/boot.gpg"
    else
        bbwarn "CIDINT_SB_KEY_DIR is not set: the boot files are NOT signed and Secure Boot will refuse them"
        printf '%s\n' '# No boot key in this build: signatures are not checked.' > ${B}/early.cfg
        PUBKEY=""
    fi

    grub-mkimage -O x86_64-efi -d "$MODDIR" -c ${B}/early.cfg -p /EFI/BOOT $PUBKEY \
        -o ${B}/out/bootx64.efi ${CIDINT_GRUB_MODULES}

    # One boot menu for every machine: the partitions are found at boot
    # time, so the menu can be signed once, here.
    cat > ${B}/out/grub.cfg <<'EOF'
serial --unit=0 --speed=115200 --word=8 --parity=no --stop=1
terminal_input console serial
terminal_output console serial
# The disk GRUB started from; system slots are its partitions 2 and 3.
regexp --set 1:cidint_disk '^([^,]+),' "$root"
probe --set cidint_uuid_a --part-uuid "($cidint_disk,gpt2)"
probe --set cidint_uuid_b --part-uuid "($cidint_disk,gpt3)"
# Boot state for A/B updates (read and written by cidint-slot):
#   slot   the committed slot
#   trial  a newly written slot that is to be tried once
#   tried  set here when the trial slot has been started
# The state file cannot be signed (it changes); only these names are read
# from it, and only the values a and b are accepted.
set slot=a
load_env -f /EFI/BOOT/grubenv --skip-sig slot trial tried rolled_back
set cidint_extra=
if [ "$check_signatures" != "enforce" ]; then
    # Extra kernel arguments from the installer, only when boot files are
    # not signature-checked (they could not be trusted otherwise).
    load_env -f /EFI/BOOT/grubenv --skip-sig extra_args
    set cidint_extra="$extra_args"
fi
if [ "$slot" != "b" ]; then set slot=a; fi
if [ "$trial" != "a" -a "$trial" != "b" ]; then set trial=; fi
set boot_slot="$slot"
if [ -n "$trial" ]; then
    if [ "$tried" = "1" ]; then
        set trial=
        set tried=
        set rolled_back=1
        save_env -f /EFI/BOOT/grubenv trial tried rolled_back
    else
        set tried=1
        save_env -f /EFI/BOOT/grubenv tried
        set boot_slot="$trial"
    fi
fi
export cidint_uuid_a cidint_uuid_b cidint_extra
set default="$boot_slot"
set timeout=3

menuentry 'CIDINT OS (system slot A)' --id a {
    linux /bzImage-a root=PARTUUID=$cidint_uuid_a @ARGS@ cidint.slot=a $cidint_extra
}
menuentry 'CIDINT OS (system slot B)' --id b {
    linux /bzImage-b root=PARTUUID=$cidint_uuid_b @ARGS@ cidint.slot=b $cidint_extra
}
EOF
    sed -i "s|@ARGS@|${CIDINT_BOOT_ARGS}|" ${B}/out/grub.cfg

    cp -L ${DEPLOY_DIR_IMAGE}/${KERNEL_IMAGETYPE} ${B}/out/bzImage
    grub-editenv ${B}/out/grubenv create
    grub-editenv ${B}/out/grubenv set slot=a

    if [ -n "$KEYS" ]; then
        for f in grub.cfg bzImage; do
            gpg --homedir "$KEYS/gnupg" --batch --yes --pinentry-mode loopback --passphrase '' \
                --digest-algo SHA512 --detach-sign -o ${B}/out/$f.sig ${B}/out/$f \
                || bbfatal "could not sign $f with the GPG boot key"
        done
        sbsign --key "$KEYS/db.key" --cert "$KEYS/db.crt" --output ${B}/out/bootx64.efi.signed \
            ${B}/out/bootx64.efi || bbfatal "could not sign GRUB for Secure Boot"
        mv ${B}/out/bootx64.efi.signed ${B}/out/bootx64.efi
        cp "$KEYS/db.cer" ${B}/out/cidint-db.cer
    fi
}

GRUB_TARGET_DIR = "x86_64-efi"

do_install() {
    install -d ${D}${datadir}/cidint/boot/EFI/BOOT
    install -m 0644 ${B}/out/bootx64.efi ${D}${datadir}/cidint/boot/EFI/BOOT/bootx64.efi
    for f in grub.cfg grub.cfg.sig grubenv; do
        [ ! -f ${B}/out/$f ] || install -m 0644 ${B}/out/$f ${D}${datadir}/cidint/boot/EFI/BOOT/$f
    done
    for f in bzImage bzImage.sig cidint-db.cer; do
        [ ! -f ${B}/out/$f ] || install -m 0644 ${B}/out/$f ${D}${datadir}/cidint/boot/$f
    done
}

do_deploy() {
    install -d ${DEPLOYDIR}/cidint-boot
    install -m 0644 ${B}/out/* ${DEPLOYDIR}/cidint-boot/
}
addtask deploy after do_install before do_build

FILES:${PN} = "${datadir}/cidint/boot"
# The kernel image and boot loader are data here, not programs to strip.
INHIBIT_PACKAGE_STRIP = "1"
INSANE_SKIP:${PN} = "arch"
