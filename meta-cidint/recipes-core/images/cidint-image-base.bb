SUMMARY = "CIDINT OS base image: read-only system, separate persistent data volume"
DESCRIPTION = "Minimal bootable CIDINT OS for x86_64 UEFI. Console only; the \
desktop, security framework and editions build on this foundation."
LICENSE = "MIT"

inherit core-image

IMAGE_FEATURES += "read-only-rootfs"

IMAGE_INSTALL = "\
    packagegroup-core-boot     kernel-modules     linux-firmware-rtl-nic     linux-firmware-rtl8168 \
    e2fsprogs-e2fsck \
    cidint-cli \
    cidint-storage \
    cidint-firewall \
    cidint-update \
    cidint-ai \
    cidint-ai-model-smollm2 \
    cidint-accounts \
    cidint-hwcheck \
    cidint-boot \
    cidint-domains \
    cryptsetup \
    ${CORE_IMAGE_EXTRA_INSTALL} \
"

IMAGE_FSTYPES = "wic wic.bmap"
WKS_FILE = "cidint-ab.wks.in"
WKS_FILE_DEPENDS:append = " e2fsprogs-native"

# Boot files on the EFI system partition (from recipes-bsp/cidint-boot):
# deployed name;name on the partition. Signatures only exist when the
# build had a boot key.
CIDINT_BOOT_SIGNED = "${@'1' if d.getVar('CIDINT_SB_KEY_DIR') else ''}"
IMAGE_BOOT_FILES = "\
    cidint-boot/bootx64.efi;EFI/BOOT/bootx64.efi \
    cidint-boot/grub.cfg;EFI/BOOT/grub.cfg \
    cidint-boot/grubenv;EFI/BOOT/grubenv \
    cidint-boot/bzImage;bzImage-a \
    cidint-boot/microcode.img;microcode-a.img \
    ${@'cidint-boot/grub.cfg.sig;EFI/BOOT/grub.cfg.sig cidint-boot/bzImage.sig;bzImage-a.sig cidint-boot/microcode.img.sig;microcode-a.img.sigcidint-boot/cidint-db.cer;EFI/cidint/cidint-db.cer' if d.getVar('CIDINT_BOOT_SIGNED') else ''} \
"
do_image_wic[depends] += "cidint-boot:do_deploy"
IMAGE_ROOTFS_EXTRA_SPACE = "0"
# The system is read-only, so its file system needs little free space; the
# image must fit a 2560 MiB system slot, and the build stops if it does not.
IMAGE_OVERHEAD_FACTOR = "1.15"
IMAGE_ROOTFS_MAXSIZE = "2621440"
