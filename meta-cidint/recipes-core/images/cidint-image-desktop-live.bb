require cidint-image-desktop.bb

SUMMARY = "CIDINT OS Desktop Edition live ISO (amnesic), with the installer"
DESCRIPTION = "Hybrid ISO that starts the graphical session from the \
read-only medium; /data and /home are in RAM. cidint-install on it installs \
the Desktop Edition."

IMAGE_FSTYPES = "iso"
APPEND:append = " ro cidint.mode=amnesic"
LABELS_LIVE = "boot"

IMAGE_INSTALL:append = " cidint-installer"
