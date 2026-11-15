require cidint-image-base.bb

SUMMARY = "CIDINT OS Live Edition (amnesic): bootable ISO, nothing is kept"
DESCRIPTION = "Hybrid ISO for x86_64 (UEFI and legacy BIOS). The system runs \
from the read-only medium; /data and /home are in RAM and are discarded \
when the session ends. Console only."

IMAGE_FSTYPES = "iso"

# Tell the system which persistence contract applies (docs/PERSISTENCE.md).
# "ro": the system image sits on a read-only medium and is mounted read-only.
APPEND:append = " ro cidint.mode=amnesic"

# Only a live boot entry. The Yocto reference installer is not offered:
# it would lay out a disk without the CIDINT data volume. Installation is
# done from the live session with cidint-install, which asks first.
LABELS_LIVE = "boot"

IMAGE_INSTALL:append = " cidint-installer"
