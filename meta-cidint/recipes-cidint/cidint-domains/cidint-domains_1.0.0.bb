SUMMARY = "CIDINT security domains: isolated micro virtual machines"
DESCRIPTION = "cidint-domain starts small virtual machines (own kernel \
instance, no network card, no disk, no shared files) with an emulator that \
runs unprivileged in a sandbox. Applications inside domains and data \
transfer between them are not implemented."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

FILESEXTRAPATHS:prepend := "${CIDINT_REPO}/domains:"
SRC_URI = "file://cidint-domain"

S = "${WORKDIR}"

inherit useradd

USERADD_PACKAGES = "${PN}"
GROUPADD_PARAM:${PN} = "-r kvm"
USERADD_PARAM:${PN} = "--system --user-group --no-create-home --home-dir /nonexistent --shell /sbin/nologin -G kvm cidint-domain"

# The domain's kernel and system image come from this build's own output.
PACKAGE_ARCH = "${MACHINE_ARCH}"
do_install[depends] += "cidint-domain-initramfs:do_image_complete virtual/kernel:do_deploy"
do_install[nostamp] = "1"

do_install() {
    install -d ${D}${sbindir} ${D}${datadir}/cidint/domains ${D}${nonarch_base_libdir}/udev/rules.d
    install -m 0755 ${WORKDIR}/cidint-domain ${D}${sbindir}/cidint-domain
    install -m 0644 ${DEPLOY_DIR_IMAGE}/bzImage ${D}${datadir}/cidint/domains/bzImage
    install -m 0644 ${DEPLOY_DIR_IMAGE}/cidint-domain-initramfs-${MACHINE}.cpio.gz ${D}${datadir}/cidint/domains/base.cpio.gz
    # Hardware virtualisation only for members of the kvm group.
    echo 'KERNEL=="kvm", GROUP="kvm", MODE="0660"' > ${D}${nonarch_base_libdir}/udev/rules.d/70-cidint-kvm.rules
}

FILES:${PN} += "${datadir}/cidint/domains ${nonarch_base_libdir}/udev/rules.d"

RDEPENDS:${PN} = "qemu-system-x86-64 qemu"
# The kernel image is data for the emulator here, not code for this system.
INSANE_SKIP:${PN} += "arch"
