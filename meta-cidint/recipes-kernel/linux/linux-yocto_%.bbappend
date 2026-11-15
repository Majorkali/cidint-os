# CIDINT kernel options on top of the upstream linux-yocto LTS kernel:
# dm-crypt for the encrypted data volume and nftables for the firewall.
# Built in (not modules) so they are available before any module loads.
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI += "file://cidint-security.cfg"
