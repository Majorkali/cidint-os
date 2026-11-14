//! CIDINT storage layout and persistence-mode detection.
//!
//! The contract between the image and the tools:
//!
//! * The kernel command line carries `cidint.mode=installed|persistent|amnesic`.
//! * Persistent state lives under `/data`, a dedicated filesystem that is
//!   separate from the read-only system image and is never formatted by
//!   an update.
//!
//! Nothing here trusts the declared mode on its own: [`report`] checks
//! what each path is really mounted on, so an image that claims to be
//! persistent while `/data` sits on a RAM disk is reported as a failure.

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    /// Installed to disk; all volumes persistent.
    Installed,
    /// Live medium with a dedicated persistent data volume.
    LivePersistent,
    /// Live medium; session data is intentionally discarded.
    LiveAmnesic,
    /// Not a CIDINT image, or the boot entry did not declare a mode.
    Undeclared,
}

impl Mode {
    pub fn expects_persistence(self) -> bool {
        matches!(self, Mode::Installed | Mode::LivePersistent)
    }

    pub fn describe(self) -> &'static str {
        match self {
            Mode::Installed => "Installed system: data is kept across restarts.",
            Mode::LivePersistent => {
                "Persistent live mode: data on the persistent volume is kept across restarts."
            }
            Mode::LiveAmnesic => {
                "Amnesic live mode: NOTHING from this session is kept after shutdown."
            }
            Mode::Undeclared => "No CIDINT persistence mode was declared at boot.",
        }
    }
}

pub fn parse_mode(cmdline: &str) -> Mode {
    for word in cmdline.split_whitespace() {
        if let Some(v) = word.strip_prefix("cidint.mode=") {
            return match v {
                "installed" => Mode::Installed,
                "persistent" => Mode::LivePersistent,
                "amnesic" => Mode::LiveAmnesic,
                _ => Mode::Undeclared,
            };
        }
    }
    Mode::Undeclared
}

/// One persistent area of the CIDINT layout.
#[derive(Debug, Clone, Copy, Serialize)]
pub struct Volume {
    pub name: &'static str,
    pub path: &'static str,
    pub holds: &'static str,
}

pub const VOLUMES: &[Volume] = &[
    Volume {
        name: "configuration",
        path: "/data/config",
        holds: "system, service and network configuration",
    },
    Volume {
        name: "user-data",
        path: "/data/home",
        holds: "home directories and personal files",
    },
    Volume {
        name: "application-data",
        path: "/data/apps",
        holds: "installed applications, their settings and databases",
    },
    Volume {
        name: "server-data",
        path: "/data/server",
        holds: "databases, container volumes, virtual-machine disks",
    },
    Volume {
        name: "system-state",
        path: "/data/state",
        holds: "logs, audit records, service and update state",
    },
    Volume {
        name: "backup",
        path: "/data/backup",
        holds: "snapshots, backup metadata and verification records",
    },
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Mount {
    pub source: String,
    pub target: String,
    pub fstype: String,
    pub read_only: bool,
}

fn unescape(field: &str) -> String {
    // /proc/mounts escapes space, tab, newline and backslash as octal.
    field
        .replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
}

pub fn parse_mounts(text: &str) -> Vec<Mount> {
    text.lines()
        .filter_map(|line| {
            let mut f = line.split_whitespace();
            let (source, target, fstype, opts) = (f.next()?, f.next()?, f.next()?, f.next()?);
            Some(Mount {
                source: unescape(source),
                target: unescape(target),
                fstype: fstype.to_string(),
                read_only: opts.split(',').any(|o| o == "ro"),
            })
        })
        .collect()
}

/// The mount that actually backs `path` (longest matching mount point;
/// a later mount on the same point hides an earlier one).
pub fn backing_mount<'a>(mounts: &'a [Mount], path: &str) -> Option<&'a Mount> {
    let mut best: Option<&Mount> = None;
    for m in mounts {
        let t = m.target.as_str();
        let covers = t == "/"
            || path == t
            || (path.starts_with(t) && path.as_bytes().get(t.len()) == Some(&b'/'));
        if covers && best.map_or(true, |b| t.len() >= b.target.len()) {
            best = Some(m);
        }
    }
    best
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Durability {
    /// Journaling or copy-on-write filesystem on a block device.
    Durable,
    /// RAM-backed; contents vanish at power-off.
    Volatile,
    /// Cannot be decided from mount data alone (overlay, network, FUSE).
    Indeterminate,
}

pub fn classify(fstype: &str) -> Durability {
    match fstype {
        "ext4" | "ext3" | "xfs" | "btrfs" | "f2fs" | "zfs" | "bcachefs" => Durability::Durable,
        "tmpfs" | "ramfs" | "devtmpfs" | "rootfs" => Durability::Volatile,
        _ => Durability::Indeterminate,
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct VolumeStatus {
    pub name: &'static str,
    pub path: &'static str,
    pub holds: &'static str,
    pub device: Option<String>,
    pub fstype: Option<String>,
    pub read_only: Option<bool>,
    pub durability: Option<Durability>,
    /// On the same filesystem as `/`, i.e. not separated from the system image.
    pub shares_root_filesystem: bool,
    pub persistent: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct Report {
    pub mode: Mode,
    pub mode_description: &'static str,
    pub volumes: Vec<VolumeStatus>,
    /// The declared mode and the real mounts agree.
    pub consistent: bool,
    pub problems: Vec<String>,
}

pub fn report(cmdline: &str, mounts_text: &str) -> Report {
    let mode = parse_mode(cmdline);
    let mounts = parse_mounts(mounts_text);
    let root = backing_mount(&mounts, "/");
    let mut problems = Vec::new();
    let mut volumes = Vec::new();

    for v in VOLUMES {
        let m = backing_mount(&mounts, v.path);
        let durability = m.map(|m| classify(&m.fstype));
        let shares_root = match (m, root) {
            (Some(m), Some(r)) => m.target == r.target,
            _ => false,
        };
        let persistent = matches!(durability, Some(Durability::Durable))
            && m.is_some_and(|m| !m.read_only)
            && !shares_root;
        if mode.expects_persistence() && !persistent {
            let why = match (m, durability) {
                (None, _) => "has no backing mount".to_string(),
                (Some(m), _) if shares_root => {
                    format!(
                        "is on the system filesystem ({}), not a separate data volume",
                        m.fstype
                    )
                }
                (Some(m), Some(Durability::Volatile)) => {
                    format!("is on {} and will be lost at power-off", m.fstype)
                }
                (Some(m), _) if m.read_only => "is mounted read-only".to_string(),
                (Some(m), _) => format!("is on {}, whose durability cannot be confirmed", m.fstype),
            };
            problems.push(format!("{} ({}) {why}", v.name, v.path));
        }
        if mode == Mode::LiveAmnesic && persistent {
            problems.push(format!(
                "{} ({}) is on persistent storage although amnesic mode was requested",
                v.name, v.path
            ));
        }
        volumes.push(VolumeStatus {
            name: v.name,
            path: v.path,
            holds: v.holds,
            device: m.map(|m| m.source.clone()),
            fstype: m.map(|m| m.fstype.clone()),
            read_only: m.map(|m| m.read_only),
            durability,
            shares_root_filesystem: shares_root,
            persistent,
        });
    }
    if mode == Mode::Undeclared {
        problems.push("kernel command line has no valid cidint.mode= entry".to_string());
    }
    Report {
        mode,
        mode_description: mode.describe(),
        consistent: problems.is_empty(),
        volumes,
        problems,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const INSTALLED: &str = "\
/dev/root / ext4 ro,relatime 0 0
proc /proc proc rw 0 0
tmpfs /run tmpfs rw,nosuid 0 0
/dev/sda4 /data ext4 rw,relatime 0 0
tmpfs /tmp tmpfs rw 0 0
";

    #[test]
    fn mode_parsing() {
        assert_eq!(
            parse_mode("quiet cidint.mode=amnesic ro"),
            Mode::LiveAmnesic
        );
        assert_eq!(parse_mode("cidint.mode=persistent"), Mode::LivePersistent);
        assert_eq!(
            parse_mode("root=/dev/sda2 cidint.mode=installed"),
            Mode::Installed
        );
        assert_eq!(parse_mode("root=/dev/sda2"), Mode::Undeclared);
        assert_eq!(parse_mode("cidint.mode=bogus"), Mode::Undeclared);
    }

    #[test]
    fn installed_system_with_data_volume_is_consistent() {
        let r = report("cidint.mode=installed", INSTALLED);
        assert!(r.consistent, "{:?}", r.problems);
        assert!(r.volumes.iter().all(|v| v.persistent));
        assert_eq!(r.volumes[0].device.as_deref(), Some("/dev/sda4"));
    }

    #[test]
    fn claimed_persistence_on_ram_is_reported() {
        let mounts = "overlay / overlay rw 0 0\ntmpfs /data tmpfs rw 0 0\n";
        let r = report("cidint.mode=persistent", mounts);
        assert!(!r.consistent);
        assert_eq!(r.problems.len(), VOLUMES.len());
        assert!(r.problems[0].contains("lost at power-off"));
    }

    #[test]
    fn data_on_the_system_filesystem_is_not_accepted() {
        let r = report("cidint.mode=installed", "/dev/sda2 / ext4 rw 0 0\n");
        assert!(!r.consistent);
        assert!(r
            .volumes
            .iter()
            .all(|v| v.shares_root_filesystem && !v.persistent));
    }

    #[test]
    fn amnesic_mode_is_consistent_only_without_persistent_storage() {
        let ram = "overlay / overlay rw 0 0\ntmpfs /data tmpfs rw 0 0\n";
        assert!(report("cidint.mode=amnesic", ram).consistent);
        assert!(!report("cidint.mode=amnesic", INSTALLED).consistent);
    }

    #[test]
    fn longest_and_latest_mount_wins() {
        let m = parse_mounts(
            "/dev/a / ext4 rw 0 0\n/dev/b /data ext4 rw 0 0\n/dev/c /data/home xfs rw 0 0\ntmpfs /data/home tmpfs rw 0 0\n/dev/d /database ext4 rw 0 0\n",
        );
        assert_eq!(backing_mount(&m, "/data/home").unwrap().fstype, "tmpfs");
        assert_eq!(backing_mount(&m, "/data/config").unwrap().source, "/dev/b");
        assert_eq!(backing_mount(&m, "/datastore").unwrap().source, "/dev/a");
    }

    #[test]
    fn mount_paths_are_unescaped() {
        let m = parse_mounts("/dev/sdb1 /media/My\\040Disk ext4 ro,noexec 0 0\n");
        assert_eq!(m[0].target, "/media/My Disk");
        assert!(m[0].read_only);
    }
}
