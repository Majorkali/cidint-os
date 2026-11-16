# CIDINT kernel options on top of the upstream linux-yocto LTS kernel:
# dm-crypt for the encrypted data volume and nftables for the firewall
# (built in, available before any module loads), plus drivers for common
# PC graphics, sound, Wi-Fi and network hardware (modules).
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI += "file://cidint-security.cfg file://cidint-hardware.cfg"
