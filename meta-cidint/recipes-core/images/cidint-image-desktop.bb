require cidint-image-base.bb

SUMMARY = "CIDINT OS Desktop Edition (preview): graphical Wayland session"
DESCRIPTION = "The base system plus a graphical session that starts at \
boot. Software rendering, no login screen; a preview, not the planned desktop."

IMAGE_INSTALL:append = " cidint-desktop ${CIDINT_DESKTOP_FIRMWARE}"

# Firmware for common PC graphics, Wi-Fi and Bluetooth chips (loaded only
# when the hardware is present).
CIDINT_DESKTOP_FIRMWARE = "    linux-firmware-i915 linux-firmware-amdgpu linux-firmware-radeon linux-firmware-nvidia-gpu     linux-firmware-iwlwifi-misc linux-firmware-iwlwifi-9000 linux-firmware-iwlwifi-9260     linux-firmware-iwlwifi-8265 linux-firmware-iwlwifi-8000c linux-firmware-iwlwifi-7265d     linux-firmware-iwlwifi-7260 linux-firmware-iwlwifi-3160-17     linux-firmware-ath9k linux-firmware-ath10k linux-firmware-ath11k     linux-firmware-rtl8821 linux-firmware-rtl8822 linux-firmware-rtl8723 linux-firmware-rtl8188     linux-firmware-rtl8192ce linux-firmware-rtl8192cu     linux-firmware-mediatek linux-firmware-mt7601u linux-firmware-mt76x2     linux-firmware-ibt-misc "

# Weston's own start-up package expects PAM and would install a second,
# conflicting session service.
BAD_RECOMMENDATIONS += "weston-init"
