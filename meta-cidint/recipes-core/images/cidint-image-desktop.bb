require cidint-image-base.bb

SUMMARY = "CIDINT OS Desktop Edition (preview): graphical Wayland session"
DESCRIPTION = "The base system plus a graphical session that starts at \
boot."

IMAGE_INSTALL:append = " cidint-desktop ${CIDINT_DESKTOP_FIRMWARE} bluez5-noinst-tools pciutils usbutils"

# Decoders for patent-encumbered formats (H.264, H.265, AAC): only when the
# build is made with kas/codecs-patented.yml, which explains the licensing.
CIDINT_PATENTED_CODECS ??= "0"
IMAGE_INSTALL:append = "${@' gstreamer1.0-libav' if d.getVar('CIDINT_PATENTED_CODECS') == '1' else ''}"

# Firmware for common PC graphics, Wi-Fi and Bluetooth chips and the audio
# DSP of Intel laptops (loaded only when the hardware is present).
CIDINT_DESKTOP_FIRMWARE = "    linux-firmware-i915 linux-firmware-amdgpu linux-firmware-radeon linux-firmware-nvidia-gpu     linux-firmware-iwlwifi-misc linux-firmware-iwlwifi-9000 linux-firmware-iwlwifi-9260     linux-firmware-iwlwifi-8265 linux-firmware-iwlwifi-8000c linux-firmware-iwlwifi-7265d     linux-firmware-iwlwifi-7260 linux-firmware-iwlwifi-3160-17     linux-firmware-ath9k linux-firmware-ath10k linux-firmware-ath11k     linux-firmware-rtl8821 linux-firmware-rtl8822 linux-firmware-rtl8723 linux-firmware-rtl8188     linux-firmware-rtl8192ce linux-firmware-rtl8192cu     linux-firmware-mediatek linux-firmware-mt7601u linux-firmware-mt76x2     linux-firmware-ibt-misc linux-firmware-ibt-17 linux-firmware-ibt-20     linux-firmware-ath12k linux-firmware-ath3k linux-firmware-qca linux-firmware-rtl8761     linux-firmware-bcm4350 linux-firmware-bcm4356-pcie linux-firmware-bcm43602 linux-firmware-bcm4371     linux-firmware-sd8897 linux-firmware-sd8997 linux-firmware-cirrus     sof-firmware "

# Weston's own start-up package expects PAM and would install a second,
# conflicting session service.
BAD_RECOMMENDATIONS += "weston-init"
