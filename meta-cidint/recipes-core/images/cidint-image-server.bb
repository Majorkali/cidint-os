require cidint-image-base.bb

SUMMARY = "CIDINT OS Server Edition: console system with SSH administration"
DESCRIPTION = "The base system plus an SSH daemon that accepts key-based \
logins only. The firewall opens the SSH port and nothing else."

IMAGE_INSTALL:append = " cidint-server"

# Use CIDINT's own daemon configuration. The stock files are edited by the
# development image features (empty passwords, root password logins); this
# one is not, and it keeps the host key on the data volume.
cidint_server_ssh() {
    install -d ${IMAGE_ROOTFS}${sysconfdir}/default
    echo "SSHD_OPTS='-f ${sysconfdir}/ssh/sshd_config_cidint'" > ${IMAGE_ROOTFS}${sysconfdir}/default/ssh
}
ROOTFS_POSTPROCESS_COMMAND:append = " cidint_server_ssh"

# A server does not need the included language model (145 MB); cidint-ai
# can still use a model placed in /data/apps/ai/models.
IMAGE_INSTALL:remove = "cidint-ai-model-smollm2"
