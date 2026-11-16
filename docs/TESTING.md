# Testing

Only tests that were actually executed are recorded as results here.

## Test layers

| Layer | Where | Runs on |
|---|---|---|
| Unit tests | `services/cidint-core/src/*.rs` | any host |
| CLI end-to-end tests (real binary, one process per command) | `cli/cidint/tests/cli.rs` | any host |
| Lint and formatting | `cargo clippy -D warnings`, `cargo fmt --check` | any host |
| Image boot, reboot and power-loss | `tests/qemu/persistence_boot_test.py` | Linux with QEMU + OVMF |
| Physical reference hardware | none yet | - |

## What the automated tests cover

State store: data survives reopen; a torn final write is discarded while
earlier commits stay; a flipped bit in the log is detected; a crash between
snapshot and log truncation is harmless; a corrupt snapshot is an error, not
a reset.

Backup: byte-identical round trip; duplicate content stored once; second
backup incremental with the first still restorable; damaged object fails
both verify and restore; restore refuses to overwrite and writes nothing on
refusal; hostile manifest paths rejected; repository inside the source
refused.

Audit: chain verifies; an edited record and a removed record are both
detected; a broken chain cannot be extended.

Layout: mode parsing; persistence claimed on RAM is reported; data on the
system filesystem is rejected; amnesic mode with persistent storage is
reported; longest and latest mount wins; escaped mount paths.

CLI: configuration persists across separate processes and is audited;
`--dry-run` creates nothing; a truncated log is recovered by the next
command; backup, verify, restore, refusal (exit 7); tampered audit log
(exit 5); export never overwrites; planned command (exit 4) and unknown
command (exit 2); Linux-only commands exit 3 on other systems.

QEMU script: UEFI boot to login; mode and volumes
consistent; system image read-only; file, marker and configuration survive
a clean reboot with a changed kernel boot id; a value committed just before
the VM is killed is present after the next boot; audit chain verifies; no
failed units.

## Recorded results

### 2026-11-14, development host

- Host: Windows 11 Pro 10.0.26300, x86_64, Intel Core 5 120U, 15.7 GB RAM
- Source: working tree on top of commit `317cbda` (uncommitted)

| Check | Toolchain | Result |
|---|---|---|
| `cargo test --workspace --locked` | rustc 1.100.0, `x86_64-pc-windows-gnu` | 35 passed, 0 failed (26 unit, 9 CLI) |
| `cargo test --workspace --locked` | rustc 1.75.0, `x86_64-pc-windows-gnu` | 35 passed, 0 failed |
| `cargo clippy --workspace --all-targets -- -D warnings` | 1.100.0 | clean |
| `cargo fmt --all -- --check` | 1.100.0 | clean |
| `cargo check --target x86_64-unknown-linux-musl` | 1.100.0 | compiles (type-check only, nothing executed) |
| `scripts/gen-crates-inc.py --check` | Python 3.11.9 | crate list matches `Cargo.lock` |

### 2026-11-14, GitHub Actions run 43562479096 (commit `81f717b`)

- Runner: `ubuntu-22.04`, x86_64

| Step | stable | 1.75.0 |
|---|---|---|
| Format | passed | not run (stable only) |
| Lint (`clippy -D warnings`) | passed | not run (stable only) |
| `cargo test --workspace --locked` | passed | passed |
| Crate list matches `Cargo.lock` | **failed** | **failed** |
| CLI on real Linux (`hardware inventory`, `system info`) | skipped after the failure | skipped |

The failure was in the check script, not the product: it imported `tomllib`,
which the runner's Python 3.10 does not have. Fixed in the following commit.
This run is the first execution of the unit and CLI tests on Linux.

### 2026-11-14, GitHub Actions run 43603109904 (commit `fed258f`)

- Runner: `ubuntu-22.04`, x86_64. No build cache was available: full build.
- Rust jobs (stable and 1.75.0): all steps passed, including the crate-list
  check and `cidint hardware inventory --json` / `cidint system info` on the
  runner's live Linux system.

| Step | Result |
|---|---|
| `kas build kas/cidint-x86-64-dev.yml` | passed, 5 h 0 min |
| Image | `cidint-image-base-genericx86-64.rootfs.wic`, 3,356,509,184 bytes (48 MB compressed) |
| `tests/qemu/persistence_boot_test.py` (QEMU q35, OVMF) | passed, exit code 0, 59 s for three boots |

The script exits 0 only if every check passes. The 15 checks:

1. the image reaches a login prompt under UEFI;
2. `cidint persistence status`: mode `installed`, all volumes on a separate
   durable filesystem;
3. the system image is read-only;
4. reboot marker armed;
5. user file created under `/home`;
6. configuration value changed;
7. a real reboot happened (kernel boot id changed);
8. P1: the marker survived the restart;
9. P1: the user file survived the restart;
10. P4: the configuration survived the restart;
11. a value was committed, then the VM was killed without shutdown;
12. the value committed before the cut is present after the next boot;
13. the audit chain verifies after the power cut;
14. the user file is intact after the power cut;
15. no failed units after the recovery boot.

Evidence: artifacts `qemu-test-evidence` (serial log, JSON report) and
`cidint-os-dev-image` on that run, kept by GitHub for 90 days. The per-check
report was not downloaded for this entry; the result is taken from the
script's exit status. Whether the runner used KVM is in that report.

Earlier image runs on the same day failed before any boot test passed and
are not results: workspace clash in `rust-native` (run 43564390082), wrong
image file name in the collect step (43581678019), and a test-script login
race (the run for commit `eef3053`: the image booted to a login prompt, the
first command went unanswered; the script now waits for the shell prompt).

### 2026-11-15, GitHub Actions run 43702270151 (commit `5b75591`)

First run of version 1.0.0 in which every step passed. Build with a warm
cache: 5 min. Results are read from the per-check reports (run annotations
and the downloaded `qemu-test-evidence` artifact).

- Disk image, boot/reboot/power cut (`persistence_boot_test.py`): 15/15, KVM used.
- Live ISO as CD-ROM under UEFI (`live_boot_test.py`): 10/10. Kernel command
  line carries `cidint.mode=amnesic`; mode `live_amnesic`; `/data` is tmpfs;
  no failed units; after a real reboot the session file and the
  configuration value were gone (acceptance test 15).
- Install to a blank 4.06 GiB virtual disk (`install_test.py`): 11/11. Disk
  listing; dry run changed nothing; refused without confirmation, with a
  confirmation naming another disk, with the live medium as target, and on
  the populated disk without `--erase-existing` (all exit 7); confirmed
  install produced four partitions; the installed disk, booted without the
  ISO, passed the same 15 boot/reboot/power-cut checks.

Artifacts: `cidint-os-images` (ISO 177,209,344 bytes, SHA-256
`dd88acbd...01f4b45b`; compressed disk image `4b640e55...415bd3d1`) and
`qemu-test-evidence`. Both were downloaded and the image checksums matched
the build's `SHA256SUMS`.

Failed runs before it, same day: run 43678086155 (`d02ae67`) could not
assemble the live initramfs (`/init` supplied by two packages; no test ran).
Run 43694452915 (`66b8ca7`): disk image 15/15, live ISO 10/10, install 9/10;
the installer stopped at the `dd` step with its error output discarded. The
command was changed (no `conv=fsync`, errors shown) and the next run passed;
the exact cause of the `dd` failure was not captured.

### 2026-11-15, GitHub Actions run 43747398237

First run in which the desktop and the AI engine passed. Eleven suites,
ten passed. Results read from the per-check reports; evidence (reports,
serial logs, screenshots) and the images were downloaded and the image
checksums matched the build's `SHA256SUMS`.

| Suite | Result |
|---|---|
| Base disk image: boot, reboot, power cut | 16/16 |
| Console live ISO, amnesic | 10/10 |
| Install to a blank disk | 11/11 |
| Encrypted install, unlock at boot, power cut | 14/14 install, 20/20 installed |
| Firewall with real traffic | 9/9 |
| Server Edition, SSH over the network | 11/11 |
| A/B update, commit, rollback | 15/15 |
| Desktop Edition, screen contents | 7/7 |
| Desktop live ISO | 7/7 |
| AI engine, sandbox and inference | 10/10 |
| Security domains | 14/17 |

Security domains: KVM was available inside the test VM. Domain `personal`
booted and passed every check. Domain `work` stopped during kernel
start-up (last console line: `hpet0 ... counter`) and never reported ready;
the three failed checks all follow from that. In run 43738233624 both
domains booted. Cause not established.

Failures on the way, same day, each fixed in the following commit:
firewall rule syntax and missing connection tracking in the kernel;
installer asking questions in unattended runs; `chpasswd` absent;
after a power cut systemd stopped the file-system check of the unlocked
volume and the system started without `/data` (replaced by
`cidint-data-unlock`); SSH keys looked for under root's read-only home;
Weston's PAM requirement and conflicting renderer settings; the emulator
built without device-tree support; `sit0` fallback devices in sandboxes.

Not run so far, and therefore unproven:

- Anything on physical hardware.
- Booting from the second system slot (`system-b` is empty).
- A power cut during a write to the backup repository on the image.
- Repeated runs: the install test has passed once, the live ISO test twice.
- The live ISO from USB or under legacy BIOS; the installer on real disks,
  on NVMe names, with `--erase-existing`, or with the typed confirmation.
- The contents of the SPDX archive.

## Running the tests

```bash
cargo test --workspace --locked
```

Image tests: see [BUILDING.md](BUILDING.md), section 3.

## Rules

- A feature moves to "Implemented and tested" only with an entry in the
  results section above.
- Persistence is claimed only after a reboot test on the image.
- A backup is called good only after a restore has been verified.
- Destructive tests (power cut, interrupted update) run in disposable VMs,
  on a copy of the image.
