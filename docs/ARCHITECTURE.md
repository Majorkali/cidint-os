# Architecture

## Foundation and ownership

| Layer | Source | Owner |
|---|---|---|
| Kernel | Upstream Linux LTS as packaged by Yocto 5.0 LTS (`linux-yocto`) | upstream |
| Toolchain, base userland, systemd | OpenEmbedded-Core / Poky `scarthgap`, pinned commit | upstream |
| Distro policy `cidint`, layer `meta-cidint`, image recipes, disk layout | this repository | CIDINT |
| Core library, CLI, storage units, tests, release process | this repository | CIDINT |

CIDINT is built from source by its own recipes. It is not a re-spin of a
binary distribution. A CIDINT research kernel is out of scope until it has
its own architecture and validation resources.

## Decisions

**AD-1 Yocto + kas.** Reproducible, pinned, cross-architecture image builds
with licence tracking and SPDX output. `kas` keeps the whole build
configuration in one reviewed file.

**AD-2 Read-only system, separate data volume.** The root filesystem is
mounted read-only. All persistent state is on `/data`. Updates replace a
system slot and never touch `/data`. See [PERSISTENCE.md](PERSISTENCE.md).

**AD-3 Two fixed-size system slots from day one.** The layout reserves
`system-b` now, because repartitioning installed machines later would put
user data at risk. The update manager that uses it is planned.

**AD-4 Rust for CIDINT services and the CLI**, with a small dependency set
(`serde`, `serde_json`, `sha2`, `clap`; 30 crates including transitive ones,
all pinned by checksum). The workspace must build with Rust 1.75, the
compiler in Yocto 5.0. Cryptography uses established implementations only.

**AD-5 No simulated data.** A command either reads the real system or exits
with code 3 ("unsupported here") or 4 ("not implemented"). Reserved commands
for unbuilt subsystems return 4.

**AD-6 The declared mode is not trusted.** The boot entry declares
`cidint.mode=installed|persistent|amnesic`; `cidint persistence status`
compares that with what each volume is really mounted on and fails if they
disagree.

**AD-7 Versioned machine interface.** `--json` output is wrapped in
`{"api_version":"v1","ok":...,"data"|"error":...}`. Exit codes are fixed:
0 ok, 1 failure, 2 usage, 3 unsupported here, 4 not implemented,
5 verification failed, 6 permission denied, 7 refused to protect data.

## Components in 0.2.0

```
cli/cidint                 cidint binary (clap)
services/cidint-core
  atomic    temp file + fsync + rename + directory fsync
  store     key-value state: WAL (checksummed, fdatasync per commit) + snapshot
  audit     hash-chained JSON-lines log
  backup    content-addressed repository, manifest per snapshot
  layout    volume definitions, /proc/mounts analysis, mode detection
  marker    reboot-survival proof using the kernel boot id
  hw        read-only inventory from /proc and /sys
  features  feature status registry
meta-cidint
  conf/distro/cidint.conf            distro policy
  recipes-core/images                cidint-image-base
  recipes-cidint/cidint-cli          builds the Rust workspace
  recipes-cidint/cidint-storage      data layout unit, /home bind mount
  wic/cidint-ab.wks.in               GPT layout
kas/cidint-x86-64-dev.yml            pinned build definition
tests/qemu                           boot, reboot and power-loss test
```

## Planned structure

Directories named in the project plan (`security/`, `domains/`, `desktop/`,
`themes/`, `widgets/`, `ai-engine/`, `automation/`, `installer/`, `recovery/`,
`server/` and others) are created when their first real content lands, not
as empty placeholders.

## Privileged operations (planned)

Target design: the CLI and graphical tools are unprivileged clients of a
local broker service over a Unix socket. The broker exposes a fixed set of
typed operations, checks the caller's identity and role, asks for
confirmation on destructive actions, and writes the audit record itself.
AI and automation components will only ever be clients of the same broker,
with their own narrower allowlist. Until the broker exists, the CLI does
only what the calling user is already permitted to do.
