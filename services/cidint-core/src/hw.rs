//! Hardware inventory read from the kernel (`/proc`, `/sys`).
//!
//! Read-only: nothing here loads modules, writes sysfs or touches
//! firmware. Values the kernel does not expose are reported as absent,
//! never estimated.

use crate::{CoreError, Result};
use serde::Serialize;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Serialize, PartialEq, Eq, Default)]
pub struct Cpu {
    pub vendor: Option<String>,
    pub model: Option<String>,
    pub logical_cpus: u32,
    /// `vmx` (Intel VT-x) or `svm` (AMD-V) if the CPU advertises it.
    pub virtualization: Option<String>,
    /// Security- and crypto-relevant feature flags that are present.
    pub features: Vec<String>,
}

const INTERESTING: &[&str] = &[
    "aes", "avx2", "avx512f", "sha_ni", "rdrand", "rdseed", "smep", "smap", "nx", "ibt", "shstk",
    "pku",
];

pub fn parse_cpuinfo(text: &str) -> Cpu {
    let mut cpu = Cpu::default();
    let mut flags: Option<Vec<&str>> = None;
    for line in text.lines() {
        let Some((k, v)) = line.split_once(':') else {
            continue;
        };
        let (k, v) = (k.trim(), v.trim());
        match k {
            "processor" => cpu.logical_cpus += 1,
            "vendor_id" if cpu.vendor.is_none() => cpu.vendor = Some(v.to_string()),
            "model name" if cpu.model.is_none() => cpu.model = Some(v.to_string()),
            // x86 says "flags", arm64 says "Features".
            "flags" | "Features" if flags.is_none() => flags = Some(v.split(' ').collect()),
            _ => {}
        }
    }
    if let Some(flags) = flags {
        cpu.virtualization = ["vmx", "svm"]
            .iter()
            .find(|f| flags.contains(f))
            .map(|f| f.to_string());
        cpu.features = INTERESTING
            .iter()
            .filter(|f| flags.contains(f))
            .map(|f| f.to_string())
            .collect();
    }
    cpu
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq, Default)]
pub struct Memory {
    pub total_kib: Option<u64>,
    pub available_kib: Option<u64>,
    pub swap_total_kib: Option<u64>,
    pub swap_free_kib: Option<u64>,
}

pub fn parse_meminfo(text: &str) -> Memory {
    let mut m = Memory::default();
    for line in text.lines() {
        let Some((k, v)) = line.split_once(':') else {
            continue;
        };
        let value = v.split_whitespace().next().and_then(|n| n.parse().ok());
        match k {
            "MemTotal" => m.total_kib = value,
            "MemAvailable" => m.available_kib = value,
            "SwapTotal" => m.swap_total_kib = value,
            "SwapFree" => m.swap_free_kib = value,
            _ => {}
        }
    }
    m
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct BlockDevice {
    pub name: String,
    pub size_bytes: Option<u64>,
    pub model: Option<String>,
    pub rotational: Option<bool>,
    pub removable: Option<bool>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct NetInterface {
    pub name: String,
    pub state: Option<String>,
    pub wireless: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct Inventory {
    pub cpu: Cpu,
    pub memory: Memory,
    pub block_devices: Vec<BlockDevice>,
    pub network_interfaces: Vec<NetInterface>,
    pub kvm_available: bool,
    pub tpm_present: bool,
    pub uefi_boot: bool,
    /// `Some(true/false)` under UEFI when the firmware variable is readable.
    pub secure_boot: Option<bool>,
}

fn read_trim(path: &Path) -> Option<String> {
    fs::read_to_string(path).ok().map(|s| s.trim().to_string())
}

fn read_flag(path: &Path) -> Option<bool> {
    match read_trim(path)?.as_str() {
        "0" => Some(false),
        "1" => Some(true),
        _ => None,
    }
}

fn dir_names(path: &Path) -> Vec<String> {
    let mut names: Vec<String> = fs::read_dir(path)
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .filter_map(|e| e.file_name().into_string().ok())
                .collect()
        })
        .unwrap_or_default();
    names.sort();
    names
}

fn secure_boot_state() -> Option<bool> {
    // EFI variable: 4 attribute bytes followed by one data byte.
    let dir = Path::new("/sys/firmware/efi/efivars");
    let name = dir_names(dir)
        .into_iter()
        .find(|n| n.starts_with("SecureBoot-"))?;
    let bytes = fs::read(dir.join(name)).ok()?;
    bytes.get(4).map(|b| *b == 1)
}

/// Collect the inventory of the running machine.
pub fn inventory() -> Result<Inventory> {
    let cpuinfo = fs::read_to_string("/proc/cpuinfo").map_err(|_| {
        CoreError::Unsupported("hardware inventory needs a Linux kernel (/proc, /sys)".into())
    })?;
    let meminfo = fs::read_to_string("/proc/meminfo").unwrap_or_default();

    let block = Path::new("/sys/block");
    let block_devices = dir_names(block)
        .into_iter()
        .filter(|n| !(n.starts_with("loop") || n.starts_with("ram")))
        .map(|name| {
            let d = block.join(&name);
            BlockDevice {
                size_bytes: read_trim(&d.join("size"))
                    .and_then(|s| s.parse::<u64>().ok())
                    .map(|sectors| sectors * 512),
                model: read_trim(&d.join("device/model")).filter(|m| !m.is_empty()),
                rotational: read_flag(&d.join("queue/rotational")),
                removable: read_flag(&d.join("removable")),
                name,
            }
        })
        .collect();

    let net = Path::new("/sys/class/net");
    let network_interfaces = dir_names(net)
        .into_iter()
        .filter(|n| n != "lo")
        .map(|name| NetInterface {
            state: read_trim(&net.join(&name).join("operstate")),
            wireless: net.join(&name).join("wireless").exists()
                || net.join(&name).join("phy80211").exists(),
            name,
        })
        .collect();

    Ok(Inventory {
        cpu: parse_cpuinfo(&cpuinfo),
        memory: parse_meminfo(&meminfo),
        block_devices,
        network_interfaces,
        kvm_available: Path::new("/dev/kvm").exists(),
        tpm_present: !dir_names(Path::new("/sys/class/tpm")).is_empty(),
        uefi_boot: Path::new("/sys/firmware/efi").exists(),
        secure_boot: secure_boot_state(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cpuinfo_x86() {
        let text = "\
processor\t: 0
vendor_id\t: GenuineIntel
model name\t: Intel(R) Core(TM) 5 120U
flags\t\t: fpu vme nx aes avx2 vmx rdrand smep smap sha_ni

processor\t: 1
vendor_id\t: GenuineIntel
model name\t: Intel(R) Core(TM) 5 120U
flags\t\t: fpu vme nx aes avx2 vmx rdrand smep smap sha_ni
";
        let c = parse_cpuinfo(text);
        assert_eq!(c.logical_cpus, 2);
        assert_eq!(c.vendor.as_deref(), Some("GenuineIntel"));
        assert_eq!(c.model.as_deref(), Some("Intel(R) Core(TM) 5 120U"));
        assert_eq!(c.virtualization.as_deref(), Some("vmx"));
        assert_eq!(
            c.features,
            ["aes", "avx2", "sha_ni", "rdrand", "smep", "smap", "nx"]
        );
    }

    #[test]
    fn cpuinfo_without_flags_reports_nothing_rather_than_guessing() {
        let c = parse_cpuinfo("processor : 0\nBogoMIPS : 48.00\n");
        assert_eq!(c.logical_cpus, 1);
        assert_eq!(c.model, None);
        assert_eq!(c.virtualization, None);
        assert!(c.features.is_empty());
    }

    #[test]
    fn meminfo() {
        let m = parse_meminfo(
            "MemTotal:       16303412 kB\nMemFree:  100 kB\nMemAvailable:    9000000 kB\nSwapTotal:             0 kB\nSwapFree: 0 kB\n",
        );
        assert_eq!(m.total_kib, Some(16303412));
        assert_eq!(m.available_kib, Some(9000000));
        assert_eq!(m.swap_total_kib, Some(0));
    }
}
