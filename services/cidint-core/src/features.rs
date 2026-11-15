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
        "parser tests; reports a consistent layout on the booted image in QEMU",
    ),
    f(
        "persistence",
        "Reboot-survival marker",
        Tested,
        "unit tests; survived a real reboot in QEMU",
    ),
    f(
        "persistence",
        "Separate /data volume in image",
        Tested,
        "file and config survive reboot and power cut in QEMU; not on hardware",
    ),
    f(
        "persistence",
        "A/B system updates with rollback (cidint-slot, local update directory)",
        Untested,
        "tool and QEMU test written; checksum only, no signature verification",
    ),
    f(
        "persistence",
        "Signed updates, update server, version checks",
        Planned,
        "",
    ),
    f(
        "persistence",
        "LUKS2 encrypted data volume (installer option, passphrase at boot)",
        Untested,
        "installer option and QEMU test written; results in docs/TESTING.md",
    ),
    f(
        "persistence",
        "TPM/FIDO2 unlock, recovery keys, encrypted system slots",
        Planned,
        "",
    ),
    f(
        "security",
        "Host firewall, inbound default-deny (nftables)",
        Untested,
        "rule set and QEMU traffic test written; results in docs/TESTING.md",
    ),
    f(
        "server",
        "Server Edition image: key-only SSH, host key and authorised keys on /data",
        Untested,
        "image and QEMU network test written; results in docs/TESTING.md",
    ),
    f(
        "desktop",
        "Graphical session preview (Weston, software rendering, no login screen)",
        Untested,
        "image, live ISO and QEMU screen test written; not the planned NOVA desktop",
    ),
    f(
        "security",
        "Root password set at installation",
        Untested,
        "installer option written; the live ISO itself still has no root password",
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
        Tested,
        "parser tests; live read on a GitHub Linux runner",
    ),
    f(
        "build",
        "Yocto layer, kas config, x86_64 UEFI image",
        Tested,
        "built in CI, boots under UEFI in QEMU; no physical hardware yet",
    ),
    f(
        "build",
        "Live ISO, amnesic mode (console only)",
        Tested,
        "boots as CD-ROM under UEFI in QEMU; nothing kept after restart; USB and BIOS untried",
    ),
    f(
        "build",
        "Console installer (cidint-install) with the CIDINT disk layout",
        Tested,
        "blank virtual disk in QEMU only; real disks and --erase-existing untried",
    ),
    f(
        "build",
        "Persistent live USB, graphical installer, server and recovery ISOs",
        Planned,
        "",
    ),
    f(
        "build",
        "SBOM (SPDX) from the Yocto build",
        Untested,
        "enabled via create-spdx; archive contents not checked",
    ),
    f(
        "security",
        "SELinux enforcing, Secure Boot, measured boot",
        Planned,
        "",
    ),
    f(
        "security",
        "Security domains: isolated micro-VMs (cidint-domain start/stop/list)",
        Untested,
        "tool and QEMU test written; nothing runs inside a domain yet",
    ),
    f(
        "security",
        "Applications in domains, transfer broker between domains",
        Planned,
        "",
    ),
    f(
        "ai",
        "Local AI engine: llama.cpp inference in a no-network sandbox (cidint-ai)",
        Untested,
        "tool and QEMU test written; no model is shipped, CPU baseline only",
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
