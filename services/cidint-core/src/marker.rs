//! Reboot-survival proof.
//!
//! `arm` writes a marker containing the kernel's boot id (a random value
//! the kernel generates at every boot). `check` after a restart proves
//! two things at once: the file survived, and a real reboot happened in
//! between. A marker read back in the same boot proves nothing and is
//! reported as such.

use crate::atomic::write_atomic;
use crate::{hex, unix_now, CoreError, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};

pub const FILE_NAME: &str = "persistence-marker.json";

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
pub struct Marker {
    pub boot_id: String,
    pub armed_at: u64,
    pub checksum: String,
}

fn checksum(boot_id: &str, armed_at: u64) -> String {
    hex(&Sha256::digest(format!("{boot_id}\n{armed_at}").as_bytes()))
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "result", rename_all = "snake_case")]
pub enum Outcome {
    /// Marker intact and written during an earlier boot.
    Survived {
        armed_boot_id: String,
        armed_at: u64,
    },
    /// Marker present but no reboot has happened yet.
    SameBoot { armed_at: u64 },
    /// No marker: never armed, or the storage did not persist.
    Missing,
}

pub fn current_boot_id() -> Result<String> {
    fs::read_to_string("/proc/sys/kernel/random/boot_id")
        .map(|s| s.trim().to_string())
        .map_err(|_| CoreError::Unsupported("the kernel boot id is only available on Linux".into()))
}

pub fn path_in(dir: &Path) -> PathBuf {
    dir.join(FILE_NAME)
}

pub fn arm(dir: &Path, boot_id: &str) -> Result<Marker> {
    fs::create_dir_all(dir)?;
    let armed_at = unix_now();
    let marker = Marker {
        boot_id: boot_id.to_string(),
        armed_at,
        checksum: checksum(boot_id, armed_at),
    };
    write_atomic(&path_in(dir), &serde_json::to_vec_pretty(&marker)?)?;
    Ok(marker)
}

pub fn check(dir: &Path, current_boot_id: &str) -> Result<Outcome> {
    let bytes = match fs::read(path_in(dir)) {
        Ok(b) => b,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Outcome::Missing),
        Err(e) => return Err(e.into()),
    };
    let m: Marker = serde_json::from_slice(&bytes)?;
    if m.checksum != checksum(&m.boot_id, m.armed_at) {
        return Err(CoreError::Corrupt("persistence marker is damaged".into()));
    }
    Ok(if m.boot_id == current_boot_id {
        Outcome::SameBoot {
            armed_at: m.armed_at,
        }
    } else {
        Outcome::Survived {
            armed_boot_id: m.boot_id,
            armed_at: m.armed_at,
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::TempDir;

    #[test]
    fn distinguishes_missing_same_boot_and_survived() {
        let t = TempDir::new("marker");
        assert_eq!(check(&t.0, "boot-a").unwrap(), Outcome::Missing);
        let m = arm(&t.0, "boot-a").unwrap();
        assert_eq!(
            check(&t.0, "boot-a").unwrap(),
            Outcome::SameBoot {
                armed_at: m.armed_at
            }
        );
        assert!(matches!(
            check(&t.0, "boot-b").unwrap(),
            Outcome::Survived { ref armed_boot_id, .. } if armed_boot_id == "boot-a"
        ));
    }

    #[test]
    fn damaged_marker_is_not_accepted_as_proof() {
        let t = TempDir::new("marker-dmg");
        arm(&t.0, "boot-a").unwrap();
        let p = path_in(&t.0);
        let text = fs::read_to_string(&p).unwrap().replace("boot-a", "boot-x");
        fs::write(&p, text).unwrap();
        assert!(matches!(check(&t.0, "boot-b"), Err(CoreError::Corrupt(_))));
    }
}
