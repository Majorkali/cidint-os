# Persistence

Data persistence is a release gate. This document states what is designed,
what is implemented, and what has been proven. As of 0.2.0 **no reboot test
has been run on a CIDINT image**; the guarantees below are verified at the
level of the storage code only.

## Disk layout (x86_64, GPT)

| # | Label | Mount | Content | Written by |
|---|---|---|---|---|
| 1 | `esp` | `/boot` | boot loader, kernel | image build, updates |
| 2 | `system-a` | `/` (read-only) | operating-system image | image build, updates |
| 3 | `system-b` | - | second system slot for A/B updates | updates (planned) |
| 4 | `cidint-data` | `/data` | everything that must survive | the running system |

Inside `/data`:

| Path | Holds |
|---|---|
| `/data/config` | system, service and network configuration |
| `/data/home` | home directories; bound to `/home` |
| `/data/apps` | installed applications, their settings and databases |
| `/data/server` | databases, container volumes, virtual-machine disks |
| `/data/state` | logs, audit records, service and update state |
| `/data/backup` | snapshots and verification records |

Rules:

1. An update writes only to the inactive system slot and the ESP.
2. Nothing formats `cidint-data` after installation.
3. `cidint-data-setup.service` uses `mkdir -p` only; it never removes or
   replaces anything.
4. If `/data` is missing or read-only, `/home` fails to mount. The system
   does not fall back to a temporary home directory.

## Modes

| `cidint.mode=` | Meaning | `/data` must be |
|---|---|---|
| `installed` | normal installation | a separate durable filesystem |
| `persistent` | live medium with persistence (planned) | a separate durable filesystem |
| `amnesic` | live medium, nothing kept (planned) | RAM-backed |

**Amnesic mode discards all session data at shutdown, by design.**
`cidint persistence status` shows the active mode in words and exits with
code 5 when the mounts do not match it, in either direction: persistence
claimed but storage is volatile, or amnesic requested but storage is
persistent.

## How CIDINT writes its own state

- **Whole files** (`atomic`): write a temporary file, `fsync` it, rename it
  over the target, `fsync` the directory.
- **State store** (`store`): every change is appended to a write-ahead log
  as `magic | length | payload | SHA-256` and flushed with `fdatasync`
  before the command reports success. On open, the log is replayed up to
  the first incomplete or damaged record; that tail is cut off and the
  number of discarded bytes is reported. A damaged snapshot is an error,
  never a silent reset.
- **Audit log**: one flushed line per record, each chained to the previous
  by hash.

These hold as far as the filesystem and the drive honour flush requests.
A drive that acknowledges a flush it has not performed can still lose data.

## Proving it on a machine

```bash
cidint persistence status
```

```bash
cidint persistence arm
```

Restart, then:

```bash
cidint persistence verify
```

`verify` passes only if the marker is intact **and** the kernel boot id has
changed, so a pass means a real restart happened in between.

## Persistence, backup, replication, availability, disaster recovery

- **Persistence**: data is still there after a restart. Does not protect
  against a failed drive, deletion or ransomware.
- **Backup**: an independent, versioned copy you can restore from. Only
  counts once a restore has been tested.
- **Replication**: a continuously updated copy elsewhere. Faithfully copies
  mistakes and corruption too. Not a backup.
- **High availability**: the service keeps running when a component fails.
  Says nothing about whether old data can be recovered.
- **Disaster recovery**: the practiced plan for losing a whole site.

RAID is redundancy against a drive failure. It is not a backup.

## Backup (0.2.0)

```bash
cidint storage backup /data/home --repo /mnt/external/cidint-repo
```

```bash
cidint storage restore <id> /data/restore-test --repo /mnt/external/cidint-repo
```

- Snapshots are never overwritten; identical content is stored once.
- `backup` re-reads everything it stored before reporting success.
- `restore` refuses to write if any target file exists, and re-hashes every
  restored file.
- Not yet: encryption, scheduling, remote targets, ownership and extended
  attributes, symlinks. Put the repository on a different physical device
  than the data.

## Not implemented yet

LUKS2 encryption with recovery keys, A/B updates and rollback, snapshots,
database and container volume integration, live USB persistence, the
writable configuration layer for `/etc`.
