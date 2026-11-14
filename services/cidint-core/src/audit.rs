//! Tamper-evident audit log.
//!
//! One JSON object per line. Each entry carries the hash of the previous
//! entry, so editing, removing or reordering any earlier line breaks the
//! chain. This detects tampering; it does not prevent an attacker with
//! write access from truncating the tail or rewriting the whole file.
//! Shipping entries to a remote collector is the planned mitigation.

use crate::{hex, unix_now, CoreError, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;

pub const GENESIS: &str = "0000000000000000000000000000000000000000000000000000000000000000";

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct Entry {
    pub seq: u64,
    pub ts: u64,
    pub actor: String,
    pub action: String,
    pub detail: Value,
    pub prev: String,
    pub hash: String,
}

fn entry_hash(seq: u64, ts: u64, actor: &str, action: &str, detail: &Value, prev: &str) -> String {
    // serde_json keeps object keys sorted (no `preserve_order`), so this
    // serialization is deterministic.
    let bytes = serde_json::to_vec(&(seq, ts, actor, action, detail, prev))
        .expect("tuple of plain values serializes");
    hex(&Sha256::digest(&bytes))
}

/// Read and verify the whole chain.
pub fn read_verified(path: &Path) -> Result<Vec<Entry>> {
    let text = match fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(e.into()),
    };
    let mut entries = Vec::new();
    let mut prev = GENESIS.to_string();
    for (i, line) in text.lines().enumerate() {
        let n = i + 1;
        let e: Entry = serde_json::from_str(line)
            .map_err(|err| CoreError::Corrupt(format!("audit line {n}: {err}")))?;
        if e.seq != n as u64 {
            return Err(CoreError::Corrupt(format!(
                "audit line {n}: sequence {} out of order",
                e.seq
            )));
        }
        if e.prev != prev {
            return Err(CoreError::Corrupt(format!(
                "audit line {n}: chain broken (previous entry altered or removed)"
            )));
        }
        let want = entry_hash(e.seq, e.ts, &e.actor, &e.action, &e.detail, &e.prev);
        if e.hash != want {
            return Err(CoreError::Corrupt(format!(
                "audit line {n}: content does not match its hash"
            )));
        }
        prev = e.hash.clone();
        entries.push(e);
    }
    Ok(entries)
}

/// Append one entry and flush it. Refuses to extend a broken chain.
pub fn append(path: &Path, actor: &str, action: &str, detail: Value) -> Result<Entry> {
    if let Some(dir) = path.parent() {
        if !dir.as_os_str().is_empty() {
            fs::create_dir_all(dir)?;
        }
    }
    let existing = read_verified(path)?;
    let (seq, prev) = match existing.last() {
        Some(last) => (last.seq + 1, last.hash.clone()),
        None => (1, GENESIS.to_string()),
    };
    let ts = unix_now();
    let hash = entry_hash(seq, ts, actor, action, &detail, &prev);
    let entry = Entry {
        seq,
        ts,
        actor: actor.to_string(),
        action: action.to_string(),
        detail,
        prev,
        hash,
    };
    let mut line = serde_json::to_vec(&entry)?;
    line.push(b'\n');
    let mut f = OpenOptions::new().create(true).append(true).open(path)?;
    f.write_all(&line)?;
    f.sync_data()?;
    Ok(entry)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::TempDir;
    use serde_json::json;

    #[test]
    fn chain_verifies_and_detects_edits_and_removals() {
        let t = TempDir::new("audit");
        let p = t.0.join("audit.log");
        append(&p, "alice", "config.set", json!({"key": "a"})).unwrap();
        append(&p, "alice", "config.set", json!({"key": "b"})).unwrap();
        append(&p, "root", "backup.create", json!({"id": "x"})).unwrap();
        assert_eq!(read_verified(&p).unwrap().len(), 3);

        let original = fs::read_to_string(&p).unwrap();

        // Edit a field in the middle entry.
        fs::write(&p, original.replace("\"key\":\"b\"", "\"key\":\"z\"")).unwrap();
        assert!(matches!(read_verified(&p), Err(CoreError::Corrupt(_))));

        // Remove the middle entry.
        let lines: Vec<&str> = original.lines().collect();
        fs::write(&p, format!("{}\n{}\n", lines[0], lines[2])).unwrap();
        assert!(matches!(read_verified(&p), Err(CoreError::Corrupt(_))));

        // A broken chain must not be extended.
        assert!(append(&p, "eve", "x", json!(null)).is_err());
    }
}
