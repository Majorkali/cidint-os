SUMMARY = "CIDINT local AI engine"
DESCRIPTION = "cidint-ai runs a locally stored language model with llama.cpp \
inside a sandbox without network access. No model is shipped."
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

FILESEXTRAPATHS:prepend := "${CIDINT_REPO}/ai:"
SRC_URI = "file://cidint-ai"

S = "${WORKDIR}"

inherit useradd

USERADD_PACKAGES = "${PN}"
USERADD_PARAM:${PN} = "--system --user-group --no-create-home --home-dir /nonexistent --shell /sbin/nologin cidint-ai"

do_install() {
    install -d ${D}${sbindir} ${D}${nonarch_libdir}/tmpfiles.d
    install -m 0755 ${WORKDIR}/cidint-ai ${D}${sbindir}/cidint-ai
    # Model directory on the data volume: readable by everyone, writable by root.
    echo "d /data/apps/ai/models 0755 root root -" > ${D}${nonarch_libdir}/tmpfiles.d/cidint-ai.conf
}

FILES:${PN} += "${nonarch_libdir}/tmpfiles.d/cidint-ai.conf"

RDEPENDS:${PN} = "llama-cpp-completion cidint-storage"
