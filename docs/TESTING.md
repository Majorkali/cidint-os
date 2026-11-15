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

Not run so far, and therefore unproven:

- Anything on physical hardware.
- Booting from the second system slot (`system-b` is empty).
- A power cut during a write to the backup repository on the image.
- Repeated runs: the QEMU test has passed once.
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
