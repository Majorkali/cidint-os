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

QEMU script (written, not yet run): UEFI boot to login; mode and volumes
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

Not run on this date, and therefore unproven:

- Any execution on Linux (including the Unix-only code paths: directory
  `fsync`, file modes in backups, live hardware inventory).
- The Yocto build. The recipes, distro configuration and disk layout have
  never been parsed by BitBake.
- The QEMU boot, reboot and power-loss test.
- The GitHub Actions workflow.
- Anything on physical hardware.

Reason: this host has no Linux environment (WSL, Hyper-V and the Virtual
Machine Platform are disabled) and no QEMU.

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
