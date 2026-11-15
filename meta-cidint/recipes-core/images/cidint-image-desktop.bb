require cidint-image-base.bb

SUMMARY = "CIDINT OS Desktop Edition (preview): graphical Wayland session"
DESCRIPTION = "The base system plus a graphical session that starts at \
boot. Software rendering, no login screen; a preview, not the planned desktop."

IMAGE_INSTALL:append = " cidint-desktop"

# Weston's own start-up package expects PAM and would install a second,
# conflicting session service.
BAD_RECOMMENDATIONS += "weston-init"
