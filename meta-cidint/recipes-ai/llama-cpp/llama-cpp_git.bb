SUMMARY = "llama.cpp: local inference of large language models (CPU)"
DESCRIPTION = "The llama-completion tool and libraries from llama.cpp, built \
for the generic x86_64 baseline (no AVX), without network or server parts."
HOMEPAGE = "https://github.com/ggml-org/llama.cpp"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=223b26b3c1143120c87e2b13111d3e99"

# Tag b12418, resolved on 2026-11-15.
SRC_URI = "git://github.com/ggml-org/llama.cpp.git;protocol=https;branch=master"
SRCREV = "b54752df40e4d8e53d34ef5a598336b4e4871d39"
PV = "0.0+b12418"

S = "${WORKDIR}/git"

inherit cmake

# Baseline instruction set: the image must run on any x86_64 machine, so
# nothing newer than what the distribution's tune guarantees is used.
# No HTTPS client, no server, no web UI, no downloads at build time.
EXTRA_OECMAKE = "\
    -DBUILD_SHARED_LIBS=OFF \
    -DLLAMA_BUILD_TESTS=OFF \
    -DLLAMA_BUILD_EXAMPLES=OFF \
    -DLLAMA_BUILD_SERVER=OFF \
    -DLLAMA_BUILD_APP=OFF \
    -DLLAMA_BUILD_UI=OFF \
    -DLLAMA_USE_PREBUILT_UI=OFF \
    -DLLAMA_BUILD_HTML=OFF \
    -DLLAMA_CONNECT=OFF \
    -DLLAMA_OPENSSL=OFF \
    -DLLAMA_BUILD_TOOLS=ON \
    -DLLAMA_TOOLS_INSTALL=ON \
    -DGGML_NATIVE=OFF \
    -DGGML_SSE42=OFF \
    -DGGML_AVX=OFF \
    -DGGML_AVX2=OFF \
    -DGGML_BMI2=OFF \
    -DGGML_FMA=OFF \
    -DGGML_F16C=OFF \
    -DGGML_OPENMP=OFF \
    -DGGML_CCACHE=OFF \
    -DLLAMA_BUILD_NUMBER=12418 \
    -DLLAMA_BUILD_COMMIT=b54752d \
"

do_install:append() {
    # Conversion scripts need Python, which the image does not carry.
    rm -f ${D}${bindir}/*.py
}

# Only the completion tool goes into the image by default.
PACKAGES =+ "${PN}-completion"
FILES:${PN}-completion = "${bindir}/llama-completion"
