SUMMARY = "CIDINT Command Intelligence (CCI)"
DESCRIPTION = "The cidint command-line interface and core library, built \
from the Rust workspace at the root of the CIDINT repository."
HOMEPAGE = "https://github.com/Majorkali/cidint-os"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

inherit cargo cargo-update-recipe-crates

FILESEXTRAPATHS:prepend := "${CIDINT_REPO}:"

SRC_URI = "\
    file://Cargo.toml \
    file://Cargo.lock \
    file://services \
    file://cli \
"

# Third-party crates, pinned by checksum. Regenerate after any change to
# Cargo.lock with: python scripts/gen-crates-inc.py
require cidint-cli-crates.inc

S = "${WORKDIR}"
CARGO_SRC_DIR = "cli/cidint"
