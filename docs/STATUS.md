# Status, roadmap and release gates

Release: **1.0.0**, 2026-11-15. Development build, not production-ready.
The version number was set by the project owner. It does not mean the
planned feature set exists: most of the specification is still "Planned"
below, and no release gate has passed.

Classification used everywhere in this project:

- **Implemented and tested** - automated tests exist and were run; results in [TESTING.md](TESTING.md)
- **Implemented but not fully tested** - code exists; not yet exercised in its real environment
- **Experimental** / **Planned** / **Unsupported**

The authoritative list is `services/cidint-core/src/features.rs`
(`cidint features`). Summary:

## Implemented and tested

- Atomic durable file replacement
- Crash-consistent state store (write-ahead log + snapshot)
- Persistence-mode and volume detection (fixtures, and on the booted image)
- Versioned deduplicating backup, verify, restore
- Hash-chained audit log, verify, export
- `cidint` CLI: JSON envelope (API v1), `--dry-run`, stable exit codes, shell completions

- Yocto distro, layer and `cidint-image-base`: builds in CI, boots under UEFI in QEMU
- Disk layout with a read-only system and a separate `/data` volume; `/home` on `/data`
- Reboot-survival marker, file and configuration survival across a real reboot
- Committed state and audit chain intact after a simulated power cut
- Hardware inventory on a live Linux system (GitHub runner)
- Live ISO in amnesic mode: boots under UEFI in QEMU, `/data` in RAM, session
  file and configuration gone after a restart
- Console installer `cidint-install`: refuses unconfirmed, mismatched,
  live-medium and already-populated targets; installs to a blank virtual
  disk; the installed system passes the reboot and power-loss tests
- Encrypted data volume (LUKS2, `--encrypt-data`): passphrase at every start,
  survives reboot and power cut, file text not readable on the raw disk
- Root password set at installation (stored as a SHA-512 hash, login requires it)
- Firewall (nftables): outbound allowed, an inbound connection is dropped
- A/B updates (`cidint-slot`): damaged update refused, update to the spare
  slot, trial start, automatic commit, rollback of an unconfirmed trial after
  a power cut; user data unchanged. Checksums only, no signatures
- Server Edition image: SSH with keys only, host key and authorised keys on `/data`
- Desktop Edition preview: Weston session as an unprivileged user, wallpaper,
  panel and terminal seen on the real screen contents; also from a live ISO
- Local AI engine (`cidint-ai`): sandbox without network or write access;
  generated text from a 1 MB test model. No model is shipped

Tested on: Windows 11 x86_64 development host (Rust 1.100.0 and 1.75.0),
GitHub `ubuntu-22.04` runners, and the image in QEMU (q35, OVMF). The image
latest full run is 43747398237 (10 of 11 suites passed). The newer suites
(desktop, AI) have passed once. Nothing has run on physical hardware.

## Experimental

- Security domains (`cidint-domain`): isolated micro-VMs with no network card
  and no disk. In three runs, one domain start hung during its kernel
  start-up. Nothing runs inside a domain and there is no transfer broker

## Implemented but not fully tested

- Installing the Desktop Edition from its live ISO (the ISO boots; the
  install from it was not run)
- Updates to a different system version (the test re-installs the same one)
- SPDX SBOM generation (enabled in the build; archive contents not checked)
- The image, the live ISO and the installer on physical hardware (never tried)
- Live ISO from a USB stick and under legacy BIOS (only CD-ROM under UEFI tried)
- Installer on a disk that already holds data with `--erase-existing`, on
  NVMe device names, and its interactive typed confirmation (not exercised)

## Planned (nothing exists yet)

Signed updates and an update server · TPM/FIDO2 unlock, recovery keys,
encrypted system slots · privilege broker · SELinux enforcing · Secure/measured
boot · applications inside domains and a transfer broker · privacy modes and
kill switch · NOVA desktop (login screen, settings, file manager, themes,
widgets), hardware-accelerated graphics · AI beyond plain text generation ·
CASME automation · CUAEE application engine · identity (MFA, LDAP, Kerberos)
· server databases, containers, HA · persistent live USB, graphical installer,
server, recovery and minimal ISOs · architectures other than x86_64 · accessibility and localisation.

## Unsupported

iOS and legacy BlackBerry as host platforms.

## Known limitations of 1.0.0

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
| 0 | Architecture, repository, threat model, build system, CI | done |
| 1 | Bootable x86_64 UEFI image | builds and boots in QEMU; physical hardware untested |
| 2 | Persistent data foundation | reboot and power-loss tests pass in QEMU; encryption, updates and filesystem checks missing |
| 12 (part) | Live ISO and console installer | amnesic live ISO and `cidint-install` pass in QEMU; graphical installer, persistent USB, other editions planned |
| 3-13 | Desktop, security, automation, domains, widgets, apps, AI, server, qualification | planned |

## Mandatory persistence acceptance tests (section 3.11)

| # | Test | State |
|---|---|---|
| 1 | File survives reboot | **passed** in QEMU (run 43603109904) |
| 2 | Theme survives reboot | blocked: no desktop |
| 3 | Installed application survives reboot | blocked: no application manager |
| 4 | Configuration change survives reboot | **passed** in QEMU for the CIDINT config store (run 43603109904) |
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
| 15 | Amnesic mode does not persist | **passed** in QEMU for a file and a configuration value (run 43702270151) |

## Production release gates (section 39)

None of the twelve gates has passed. Gate 1 (bootability) has QEMU evidence
only and needs reference hardware; Gate 3 (persistence) has 2 of its 15
acceptance tests passing.
