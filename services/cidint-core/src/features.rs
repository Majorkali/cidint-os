//! Honest feature status registry.
//!
//! Single source of truth for `cidint features` and docs/STATUS.md.
//! Promote an entry only with test evidence recorded in docs/TESTING.md.

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    /// Implemented; automated tests exist and have been run.
    Tested,
    /// Implemented; not yet exercised in its real environment.
    Untested,
    Experimental,
    Planned,
    Unsupported,
}

impl Status {
    pub fn label(self) -> &'static str {
        match self {
            Status::Tested => "Implemented and tested",
            Status::Untested => "Implemented but not fully tested",
            Status::Experimental => "Experimental",
            Status::Planned => "Planned",
            Status::Unsupported => "Unsupported",
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize)]
pub struct Feature {
    pub area: &'static str,
    pub name: &'static str,
    pub status: Status,
    pub note: &'static str,
}

const fn f(area: &'static str, name: &'static str, status: Status, note: &'static str) -> Feature {
    Feature {
        area,
        name,
        status,
        note,
    }
}

use Status::*;

pub const FEATURES: &[Feature] = &[
    f(
        "persistence",
        "Atomic durable file replacement",
        Tested,
        "unit tests",
    ),
    f(
        "persistence",
        "Crash-consistent state store (WAL + snapshot)",
        Tested,
        "unit tests incl. torn write, bit flip, interrupted checkpoint",
    ),
    f(
        "persistence",
        "Persistence-mode and volume detection",
        Tested,
        "parser tests on mount-table fixtures; not yet run on a booted image",
    ),
    f(
        "persistence",
        "Reboot-survival marker",
        Untested,
        "logic unit-tested; needs a real reboot in QEMU",
    ),
    f(
        "persistence",
        "Separate /data volume in image",
        Untested,
        "wic layout written; first CI build pending",
    ),
    f(
        "persistence",
        "A/B system updates with rollback",
        Planned,
        "two root slots are reserved in the disk layout",
    ),
    f(
        "persistence",
        "LUKS2 encrypted data volume, TPM/FIDO2 unlock",
        Planned,
        "",
    ),
    f(
        "backup",
        "Versioned deduplicating backup, verify, restore",
        Tested,
        "unit + CLI tests; restore re-hashes every file",
    ),
    f(
        "backup",
        "Encrypted, scheduled, remote backups",
        Planned,
        "",
    ),
    f(
        "audit",
        "Hash-chained audit log, verify, export",
        Tested,
        "unit + CLI tests",
    ),
    f(
        "cli",
        "cidint CLI: JSON output, dry-run, exit codes, completions",
        Tested,
        "integration tests on the built binary",
    ),
    f(
        "cli",
        "Privilege broker service",
        Planned,
        "CLI currently acts with the caller's own permissions",
    ),
    f(
        "hardware",
        "Hardware inventory from /proc and /sys",
        Untested,
        "parsers unit-tested; live read needs Linux",
    ),
    f(
        "build",
        "Yocto layer, kas config, x86_64 UEFI image",
        Untested,
        "written; not yet built or booted",
    ),
    f(
        "build",
        "Live / installer / server / recovery ISOs",
        Planned,
        "",
    ),
    f(
        "build",
        "SBOM (SPDX) from the Yocto build",
        Untested,
        "enabled via create-spdx; produced by the first build",
    ),
    f(
        "security",
        "SELinux enforcing, Secure Boot, measured boot",
        Planned,
        "",
    ),
    f(
        "security",
        "Security domains (KVM), transfer broker",
        Planned,
        "",
    ),
    f(
        "privacy",
        "Tor/VPN modes, fail-closed kill switch",
        Planned,
        "the retired 0.1 prototype had a Tor mode; not carried over",
    ),
    f(
        "desktop",
        "NOVA themes, widgets, Plasma/Wayland desktop",
        Planned,
        "",
    ),
    f("ai", "CNIE local AI engine", Planned, ""),
    f("automation", "CASME automation engine", Planned, ""),
    f("apps", "CUAEE application engine", Planned, ""),
    f(
        "server",
        "Server edition: databases, containers, HA",
        Planned,
        "",
    ),
    f(
        "platform",
        "Architectures other than x86_64",
        Planned,
        "no build or boot evidence yet",
    ),
    f(
        "platform",
        "iOS and legacy BlackBerry as host platforms",
        Unsupported,
        "",
    ),
];

/// Top-level commands reserved in the CLI whose subsystems are not built yet.
pub const PLANNED_COMMANDS: &[&str] = &[
    "security",
    "privacy",
    "network",
    "domain",
    "app",
    "database",
    "theme",
    "widget",
    "automation",
    "ai",
    "update",
    "recovery",
];
