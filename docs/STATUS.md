# Status, roadmap and release gates

Release: **0.2.0 "foundation"**, 2026-11-14. Development build.

Classification used everywhere in this project:

- **Implemented and tested** - automated tests exist and were run; results in [TESTING.md](TESTING.md)
- **Implemented but not fully tested** - code exists; not yet exercised in its real environment
- **Experimental** / **Planned** / **Unsupported**

The authoritative list is `services/cidint-core/src/features.rs`
(`cidint features`). Summary:

## Implemented and tested

- Atomic durable file replacement
- Crash-consistent state store (write-ahead log + snapshot)
- Persistence-mode and volume detection (logic; fixtures only)
- Versioned deduplicating backup, verify, restore
- Hash-chained audit log, verify, export
- `cidint` CLI: JSON envelope (API v1), `--dry-run`, stable exit codes, shell completions

Tested on: Windows 11 x86_64 development host, Rust 1.100.0 and Rust 1.75.0.
Type-checked, not executed, for `x86_64-unknown-linux-musl`.

## Implemented but not fully tested

- Yocto distro, layer and `cidint-image-base` (never built)
- Disk layout: ESP, system-a, system-b, cidint-data (never built)
- `/home` bound to `/data/home`; data directory setup unit (never booted)
- Reboot-survival marker (`cidint persistence arm` / `verify`): logic tested, no real reboot yet
- Hardware inventory: parsers tested, live read needs Linux
- QEMU boot / reboot / power-loss test script (never run)
- SPDX SBOM generation (enabled; produced by the first build)

## Planned (nothing exists yet)

A/B update manager and rollback · LUKS2 encryption, TPM/FIDO2 unlock, recovery
keys · privilege broker · SELinux enforcing · Secure/measured boot · security
domains (KVM) and transfer broker · privacy modes and kill switch · NOVA
desktop, themes and widgets · CNIE AI engine · CASME automation · CUAEE
application engine · identity (MFA, LDAP, Kerberos) · server edition
(databases, containers, HA) · live, installer, server, recovery and minimal
ISOs · architectures other than x86_64 · accessibility and localisation.

## Unsupported

iOS and legacy BlackBerry as host platforms.

## Known limitations of 0.2.0

- The CLI acts with the caller's own permissions; there is no broker yet.
- Backups are not encrypted by CIDINT; keep the repository on an encrypted volume.
- Backups skip symlinks and special files (listed in the manifest as `skipped`)
  and do not record ownership or extended attributes.
- The audit log detects edits and removals inside the chain, not truncation of
  the tail or a full rewrite by someone with write access.
- `/etc` is part of the read-only image. System configuration that must change
  at runtime needs the planned configuration layer on `/data/config`.
- The data volume is not encrypted.
- The dev image has an empty root password (`debug-tweaks`). It is a test
  image, not something to deploy.
- Layer pin: `poky` is pinned to a commit; the kas version is pinned; GitHub
  Actions are pinned by major tag, not by commit.

## Roadmap position

| Phase | Scope | State |
|---|---|---|
| 0 | Architecture, repository, threat model, build system, CI | done, except the first CI run |
| 1 | Bootable x86_64 UEFI image | written, not built |
| 2 | Persistent data foundation | core tested on host; reboot tests pending on image |
| 3-13 | Desktop, security, automation, domains, widgets, apps, AI, server, installer/ISO, qualification | planned |

## Mandatory persistence acceptance tests (section 3.11)

| # | Test | State |
|---|---|---|
| 1 | File survives reboot | automated in `tests/qemu/persistence_boot_test.py`; **not yet run** |
| 2 | Theme survives reboot | blocked: no desktop |
| 3 | Installed application survives reboot | blocked: no application manager |
| 4 | Configuration change survives reboot | automated (CIDINT config store); **not yet run** |
| 5 | Database record survives database restart | blocked: no database integration |
| 6 | Server restart keeps service data | blocked: no server edition |
| 7 | Container volume survives restart | blocked |
| 8 | VM disk survives restart | blocked |
| 9 | Controlled update keeps user data | blocked: no update manager |
| 10 | Interrupted update recovers | blocked: no update manager |
| 11 | Restore verified backup, compare records | passes on host (unit + CLI tests); not yet run on image |
| 12 | Filesystem integrity check | not yet automated |
| 13 | Encrypted storage unlock and recovery | blocked: no encryption |
| 14 | Persistent USB mode across reboots | blocked: no live edition |
| 15 | Amnesic mode does not persist | detection logic tested; blocked: no live edition |

## Production release gates (section 39)

None of the twelve gates has passed. Gate 1 (bootability) and the first part
of Gate 3 (persistence) are the targets of the next step: the first CI image
build and QEMU run.
