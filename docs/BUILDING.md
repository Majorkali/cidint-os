# Building

## 1. Rust components (any OS)

Install Rust with `rustup` (https://rustup.rs). On Windows without Visual
Studio, use the GNU toolchain:

```bash
rustup default stable-x86_64-pc-windows-gnu
```

Build and test:

```bash
cargo test --workspace --locked
```

```bash
cargo clippy --workspace --all-targets --locked -- -D warnings
```

Check against the compiler Yocto uses:

```bash
rustup toolchain install 1.75.0 --profile minimal
```

```bash
cargo +1.75.0 test --workspace --locked
```

After changing dependencies, refresh the crate list used by the Yocto recipe:

```bash
python scripts/gen-crates-inc.py
```

## 2. System image (Linux only)

The image cannot be built on Windows itself. Use GitHub Actions or a Linux
machine / WSL2 with about 60 GB free disk and 8 GB RAM. A first build
compiles the toolchain and takes several hours.

### GitHub Actions

Every push to `main` runs the **CI** workflow, which builds the
image, boots it in QEMU under UEFI, runs the reboot and power-loss tests,
and uploads the image, `SHA256SUMS`, the SPDX archive and the test evidence.

### Local Linux (Ubuntu 22.04)

```bash
sudo apt-get install -y gawk wget git diffstat unzip texinfo gcc build-essential chrpath socat cpio python3 python3-pip python3-pexpect xz-utils debianutils iputils-ping python3-git python3-jinja2 python3-subunit zstd lz4 file locales libacl1 python3-venv qemu-system-x86 ovmf
```

```bash
python3 -m venv ~/kas-venv && ~/kas-venv/bin/pip install --upgrade pip && ~/kas-venv/bin/pip install kas==4.8.2
```

```bash
~/kas-venv/bin/kas build kas/cidint-x86-64-dev.yml
```

Output: `build/tmp/deploy/images/genericx86-64/cidint-image-base-genericx86-64.rootfs.wic`

## 3. Boot and test in QEMU

```bash
python3 tests/qemu/persistence_boot_test.py build/tmp/deploy/images/genericx86-64/cidint-image-base-genericx86-64.rootfs.wic --ovmf-code /usr/share/OVMF/OVMF_CODE_4M.fd --ovmf-vars /usr/share/OVMF/OVMF_VARS_4M.fd
```

The script works on a copy of the image. Results:
`artifacts/qemu-test/persistence-report.json` and `serial.log`.

## 4. Verify a downloaded image

```bash
sha256sum -c SHA256SUMS
```

## 5. Writing to a USB drive

Not documented yet, on purpose: the image has not been booted. Once it has,
this section will give `bmaptool` instructions. Writing an image erases the
target drive; check the device name twice.

## Pinned inputs

| Input | Pin |
|---|---|
| poky (`scarthgap`) | commit `6b7c1066f778db5a6b3e35400617b10342988fa8` |
| kas | 4.8.2 |
| Rust crates | `Cargo.lock` checksums, mirrored in `cidint-cli-crates.inc` |
| Rust compiler for images | 1.75 (from Yocto 5.0) |

## Signing keys

No signing is implemented. When it is, development and release keys will be
separate, and private keys never enter this repository (`.gitignore` blocks
common key file types as a second line of defence).
