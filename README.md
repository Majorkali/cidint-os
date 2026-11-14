# CIDINT OS

**Cyber Intelligence, Defense, Innovation & Network Technology Operating System**
*Intelligence Beyond Limits.*

> **Status: 0.2.0 "foundation", development. Not production-ready.**
> What exists today is the persistence and tooling foundation, tested on a
> development host, plus a Yocto image definition that **has not been built
> or booted yet**. There is no desktop, no installer and no ISO in this
> release. The exact state of every feature is in [docs/STATUS.md](docs/STATUS.md);
> the same list is printed by `cidint features`.

## What CIDINT OS is

A security-focused, persistent operating system built with the Yocto Project
on the upstream Linux LTS kernel. CIDINT does not own the kernel, and it is
not a renamed copy of another distribution: the image recipes, storage
layout, system services, command line, policies, tests and release process
in this repository are CIDINT's own work. Upstream components are used under
their licences and listed in the SBOM that each image build produces.

The first rule of the design is that data is not lost on restart:

- the system image is read-only and lives in its own partition slot;
- everything that must survive lives on a separate `/data` volume that an
  update never formats;
- CIDINT's own state is written with a write-ahead log and atomic replacement,
  so an interrupted write is detected and rolled back to the last commit;
- a backup counts only after its content has been re-read and verified.

## What works today

| Component | Location | Evidence |
|---|---|---|
| Crash-consistent state store | `services/cidint-core/src/store.rs` | unit tests: torn write, bit flip, interrupted checkpoint |
| Atomic durable file replacement | `services/cidint-core/src/atomic.rs` | unit test |
| Versioned, deduplicating backup with verified restore | `services/cidint-core/src/backup.rs` | unit + CLI tests |
| Tamper-evident audit log | `services/cidint-core/src/audit.rs` | unit + CLI tests |
| Persistence-mode and volume checker | `services/cidint-core/src/layout.rs` | parser tests on mount-table fixtures |
| `cidint` CLI (JSON, dry-run, stable exit codes, completions) | `cli/cidint` | 9 end-to-end tests on the built binary |

Written but **not yet run** (needs the first CI image build): the Yocto layer
`meta-cidint`, the A/B + data disk layout, the `/home`-on-`/data` units, the
hardware inventory on a live system, and the QEMU reboot/power-loss test.

## Try the CLI

```bash
cargo test --workspace
```

```bash
cargo run -p cidint -- features
```

```bash
cargo run -p cidint -- --state-dir ./demo-state config set theme/name obsidian-command
```

Commands that need a running Linux system (`hardware inventory`,
`persistence status`) exit with code 3 elsewhere instead of inventing data.

## Building the image

Image builds need Linux and run on GitHub Actions (workflow **CI**, option
*build_image*). See [docs/BUILDING.md](docs/BUILDING.md).

## Documentation

- [docs/STATUS.md](docs/STATUS.md) - feature status, roadmap, release gates
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - design and decisions
- [docs/PERSISTENCE.md](docs/PERSISTENCE.md) - storage layout and guarantees
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) - what is and is not defended
- [docs/BUILDING.md](docs/BUILDING.md) - toolchain, image build, QEMU
- [docs/TESTING.md](docs/TESTING.md) - test plan and recorded results

## History

Version 0.1 was a Debian live-build prototype with Python tooling. It was
retired because CIDINT must be an independently built system rather than a
re-spin of a distribution. Its source remains in git history (commit
`317cbda` and earlier).

## Licence

CIDINT-owned code is licensed under Apache-2.0 ([LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt)).
Upstream components keep their own licences.
