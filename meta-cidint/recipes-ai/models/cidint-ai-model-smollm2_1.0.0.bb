SUMMARY = "SmolLM2-135M-Instruct language model (GGUF, Q8_0) for cidint-ai"
DESCRIPTION = "A small instruction-tuned language model by Hugging Face \
(SmolLM2-135M-Instruct), quantised to GGUF Q8_0 by bartowski. It is the \
default model of the CIDINT local AI engine. 135 million parameters: it \
answers short questions and follows simple instructions, and it is often \
wrong. It is not a source of facts."
HOMEPAGE = "https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct"
# Model weights: Apache License 2.0 (model card of the original model and
# of the GGUF conversion).
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

# Pinned to an exact repository revision and file checksum.
MODEL_FILE = "SmolLM2-135M-Instruct-Q8_0.gguf"
MODEL_REV = "09816acd5d99df7be770d85ea30822623dab342c"
SRC_URI = "https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF/resolve/${MODEL_REV}/${MODEL_FILE};downloadfilename=${MODEL_FILE};unpack=0"
SRC_URI[sha256sum] = "5a1395716f7913741cc51d98581b9b1228d80987a9f7d3664106742eb06bba83"

S = "${WORKDIR}"

inherit allarch

do_configure[noexec] = "1"
do_compile[noexec] = "1"

do_install() {
    install -d ${D}${datadir}/cidint/ai/models
    install -m 0644 ${WORKDIR}/${MODEL_FILE} ${D}${datadir}/cidint/ai/models/${MODEL_FILE}
    cat > ${D}${datadir}/cidint/ai/models/${MODEL_FILE}.NOTICE <<EOF
${MODEL_FILE}
Model:        SmolLM2-135M-Instruct by Hugging Face (HuggingFaceTB)
              https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct
GGUF file:    https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF
              revision ${MODEL_REV}
SHA-256:      5a1395716f7913741cc51d98581b9b1228d80987a9f7d3664106742eb06bba83
Licence:      Apache License 2.0 (/usr/share/common-licenses/Apache-2.0
              where installed; https://www.apache.org/licenses/LICENSE-2.0)
Not made by the CIDINT OS project. Small model: answers can be wrong.
EOF
}

FILES:${PN} = "${datadir}/cidint/ai/models"
INSANE_SKIP:${PN} += "arch"
