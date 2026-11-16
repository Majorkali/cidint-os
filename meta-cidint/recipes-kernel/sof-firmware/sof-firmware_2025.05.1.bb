SUMMARY = "Sound Open Firmware: audio DSP firmware and topologies for Intel laptops"
DESCRIPTION = "Firmware for the audio DSP of Intel processors from Apollo Lake \
on (built-in speakers and microphones of most laptops since about 2019), \
released by the Sound Open Firmware project as signed binaries. Loaded by \
the kernel's SOF driver only on matching hardware."
HOMEPAGE = "https://github.com/thesofproject/sof-bin"
LICENSE = "Firmware-SOF-Intel & Firmware-SOF-NXP"
NO_GENERIC_LICENSE[Firmware-SOF-Intel] = "LICENCE.Intel"
NO_GENERIC_LICENSE[Firmware-SOF-NXP] = "LICENCE.NXP"
LIC_FILES_CHKSUM = "file://LICENCE.Intel;md5=54b4f1a2dd35fd85bc7a1d4afa731b78 \
                    file://LICENCE.NXP;md5=eb04e5837f4ef87eaa7086f54ae58af7"

SRC_URI = "https://github.com/thesofproject/sof-bin/releases/download/v${PV}/sof-bin-${PV}.tar.gz"
SRC_URI[sha256sum] = "60d54eae32694332a2120b7c44e4af403a14fe1fdf4c58d72981102b191d259c"

S = "${WORKDIR}/sof-bin-${PV}"

inherit allarch

do_configure[noexec] = "1"
do_compile[noexec] = "1"

do_install() {
    install -d ${D}${nonarch_base_libdir}/firmware/intel
    for d in sof sof-tplg sof-ipc4 sof-ipc4-tplg sof-ipc4-lib sof-ace-tplg; do
        [ -e ${S}/$d ] || continue
        cp -R --no-dereference --preserve=mode,links ${S}/$d ${D}${nonarch_base_libdir}/firmware/intel/
    done
    install -d ${D}${datadir}/licenses/sof-firmware
    install -m 0644 ${S}/LICENCE.Intel ${S}/LICENCE.NXP ${D}${datadir}/licenses/sof-firmware/
}

FILES:${PN} = "${nonarch_base_libdir}/firmware/intel ${datadir}/licenses/sof-firmware"
# Firmware for the DSP, not for this processor.
INSANE_SKIP:${PN} = "arch already-stripped ldflags file-rdeps"
INHIBIT_PACKAGE_STRIP = "1"
INHIBIT_SYSROOT_STRIP = "1"
