//! Crash-consistent key-value state store.
//!
//! Layout inside the store directory:
//!
//! * `snapshot.json` - full state at sequence number `seq`, replaced atomically.
//! * `wal.log`       - write-ahead log of operations after the snapshot.
//!
//! Each WAL record is `MAGIC | len:u32le | payload | sha256(payload)`.
//! A commit returns only after the record is flushed with `fdatasync`.
//! On open the log is replayed up to the first incomplete or damaged
//! record; that torn tail (an interrupted write) is cut off and reported.
//! Records at or below the snapshot sequence are skipped, so a crash
//! between writing a snapshot and truncating the log is harmless.

use crate::atomic::write_atomic;
use crate::{CoreError, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

const MAGIC: &[u8; 4] = b"CDW1";
const HEADER: usize = 8;
const TRAILER: usize = 32;
const MAX_RECORD: usize = 16 * 1024 * 1024;

#[derive(Serialize, Deserialize, Debug, Clone)]
enum Op {
    Set { key: String, value: Value },
    Delete { key: String },
}

#[derive(Serialize, Deserialize)]
struct Record {
    seq: u64,
    op: Op,
}

#[derive(Serialize, Deserialize, Default)]
struct Snapshot {
    seq: u64,
    data: BTreeMap<String, Value>,
}

/// What recovery found when the store was opened.
#[derive(Debug, Default, Clone, Serialize, PartialEq, Eq)]
pub struct Recovery {
    /// Operations re-applied from the log.
    pub replayed: u64,
    /// Log records already contained in the snapshot.
    pub skipped_stale: u64,
    /// Bytes of torn or damaged log tail that were discarded.
    pub discarded_bytes: u64,
}

pub struct Store {
    dir: PathBuf,
    wal: File,
    seq: u64,
    data: BTreeMap<String, Value>,
    recovery: Recovery,
}

impl Store {
    pub fn open(dir: &Path) -> Result<Store> {
        fs::create_dir_all(dir)?;
        let snap_path = dir.join("snapshot.json");
        let snap: Snapshot = match fs::read(&snap_path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| {
                // Never reset silently: the administrator must decide.
                CoreError::Corrupt(format!("{}: {e}", snap_path.display()))
            })?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Snapshot::default(),
            Err(e) => return Err(e.into()),
        };

        let wal_path = dir.join("wal.log");
        let bytes = match fs::read(&wal_path) {
            Ok(b) => b,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(e) => return Err(e.into()),
        };

        let mut data = snap.data;
        let mut seq = snap.seq;
        let mut recovery = Recovery::default();
        let mut off = 0usize;
        while let Some((rec, next)) = parse_record(&bytes, off) {
            if rec.seq <= snap.seq {
                recovery.skipped_stale += 1;
            } else if rec.seq == seq + 1 {
                apply(&mut data, rec.op);
                seq = rec.seq;
                recovery.replayed += 1;
            } else {
                break; // sequence gap: treat the rest as damaged
            }
            off = next;
        }
        recovery.discarded_bytes = (bytes.len() - off) as u64;

        let wal = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&wal_path)?;
        if recovery.discarded_bytes > 0 {
            wal.set_len(off as u64)?;
            wal.sync_all()?;
        }
        Ok(Store {
            dir: dir.to_path_buf(),
            wal,
            seq,
            data,
            recovery,
        })
    }

    pub fn recovery(&self) -> &Recovery {
        &self.recovery
    }

    pub fn seq(&self) -> u64 {
        self.seq
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        self.data.get(key)
    }

    pub fn entries(&self) -> &BTreeMap<String, Value> {
        &self.data
    }

    /// Durably set a key. Returns after the change is on stable storage.
    pub fn set(&mut self, key: &str, value: Value) -> Result<()> {
        validate_key(key)?;
        self.commit(Op::Set {
            key: key.to_string(),
            value,
        })
    }

    /// Durably delete a key. Returns whether it existed.
    pub fn delete(&mut self, key: &str) -> Result<bool> {
        if !self.data.contains_key(key) {
            return Ok(false);
        }
        self.commit(Op::Delete {
            key: key.to_string(),
        })?;
        Ok(true)
    }

    /// Fold the log into a new snapshot and empty the log.
    pub fn checkpoint(&mut self) -> Result<()> {
        let snap = serde_json::to_vec_pretty(&SnapshotRef {
            seq: self.seq,
            data: &self.data,
        })?;
        write_atomic(&self.dir.join("snapshot.json"), &snap)?;
        self.wal.set_len(0)?;
        self.wal.sync_all()?;
        Ok(())
    }

    fn commit(&mut self, op: Op) -> Result<()> {
        let rec = Record {
            seq: self.seq + 1,
            op: op.clone(),
        };
        let payload = serde_json::to_vec(&rec)?;
        if payload.len() > MAX_RECORD {
            return Err(CoreError::Invalid("value too large".into()));
        }
        let mut frame = Vec::with_capacity(HEADER + payload.len() + TRAILER);
        frame.extend_from_slice(MAGIC);
        frame.extend_from_slice(&(payload.len() as u32).to_le_bytes());
        frame.extend_from_slice(&payload);
        frame.extend_from_slice(&Sha256::digest(&payload));
        self.wal.seek(SeekFrom::End(0))?;
        self.wal.write_all(&frame)?;
        self.wal.sync_data()?;
        // Only now is the change part of the visible state.
        apply(&mut self.data, op);
        self.seq += 1;
        Ok(())
    }
}

#[derive(Serialize)]
struct SnapshotRef<'a> {
    seq: u64,
    data: &'a BTreeMap<String, Value>,
}

fn validate_key(key: &str) -> Result<()> {
    let ok = !key.is_empty()
        && key.len() <= 256
        && key
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-' | '/'));
    if ok {
        Ok(())
    } else {
        Err(CoreError::Invalid(format!(
            "key {key:?}: use 1-256 characters from [A-Za-z0-9._-/]"
        )))
    }
}

fn apply(data: &mut BTreeMap<String, Value>, op: Op) {
    match op {
        Op::Set { key, value } => {
            data.insert(key, value);
        }
        Op::Delete { key } => {
            data.remove(&key);
        }
    }
}

/// Parse one record at `off`; `None` if it is incomplete or damaged.
fn parse_record(bytes: &[u8], off: usize) -> Option<(Record, usize)> {
    let head = bytes.get(off..off + HEADER)?;
    if &head[..4] != MAGIC {
        return None;
    }
    let len = u32::from_le_bytes([head[4], head[5], head[6], head[7]]) as usize;
    if len > MAX_RECORD {
        return None;
    }
    let start = off + HEADER;
    let payload = bytes.get(start..start + len)?;
    let sum = bytes.get(start + len..start + len + TRAILER)?;
    if Sha256::digest(payload).as_slice() != sum {
        return None;
    }
    let rec: Record = serde_json::from_slice(payload).ok()?;
    Some((rec, start + len + TRAILER))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::TempDir;
    use serde_json::json;

    #[test]
    fn data_survives_reopen() {
        let t = TempDir::new("store-reopen");
        {
            let mut s = Store::open(&t.0).unwrap();
            s.set("theme/name", json!("obsidian-command")).unwrap();
            s.set("net/mode", json!("tor")).unwrap();
            assert!(s.delete("net/mode").unwrap());
        }
        let s = Store::open(&t.0).unwrap();
        assert_eq!(s.get("theme/name"), Some(&json!("obsidian-command")));
        assert_eq!(s.get("net/mode"), None);
        assert_eq!(s.recovery().replayed, 3);
        assert_eq!(s.recovery().discarded_bytes, 0);
    }

    #[test]
    fn torn_final_write_is_discarded_and_earlier_commits_kept() {
        let t = TempDir::new("store-torn");
        {
            let mut s = Store::open(&t.0).unwrap();
            s.set("a", json!(1)).unwrap();
            s.set("b", json!(2)).unwrap();
        }
        // Simulate power loss in the middle of the second record.
        let wal = t.0.join("wal.log");
        let len = fs::metadata(&wal).unwrap().len();
        let f = OpenOptions::new().write(true).open(&wal).unwrap();
        f.set_len(len - 10).unwrap();
        drop(f);

        let mut s = Store::open(&t.0).unwrap();
        assert_eq!(s.get("a"), Some(&json!(1)));
        assert_eq!(s.get("b"), None);
        assert!(s.recovery().discarded_bytes > 0);
        // The store must be writable again and stay consistent.
        s.set("c", json!(3)).unwrap();
        drop(s);
        let s = Store::open(&t.0).unwrap();
        assert_eq!(s.get("c"), Some(&json!(3)));
        assert_eq!(s.recovery().discarded_bytes, 0);
    }

    #[test]
    fn bit_flip_in_log_is_detected() {
        let t = TempDir::new("store-flip");
        {
            let mut s = Store::open(&t.0).unwrap();
            s.set("a", json!("first")).unwrap();
            s.set("b", json!("second")).unwrap();
        }
        let wal = t.0.join("wal.log");
        let mut bytes = fs::read(&wal).unwrap();
        let last = bytes.len() - 40; // inside the second record's payload
        bytes[last] ^= 0x01;
        fs::write(&wal, &bytes).unwrap();

        let s = Store::open(&t.0).unwrap();
        assert_eq!(s.get("a"), Some(&json!("first")));
        assert_eq!(s.get("b"), None, "damaged record must not be applied");
    }

    #[test]
    fn crash_between_snapshot_and_log_truncation_is_harmless() {
        let t = TempDir::new("store-ckpt");
        let wal = t.0.join("wal.log");
        let saved;
        {
            let mut s = Store::open(&t.0).unwrap();
            s.set("a", json!(1)).unwrap();
            s.set("a", json!(2)).unwrap();
            saved = fs::read(&wal).unwrap();
            s.checkpoint().unwrap();
        }
        // Put the old log back: snapshot written, truncation "never happened".
        fs::write(&wal, &saved).unwrap();
        let mut s = Store::open(&t.0).unwrap();
        assert_eq!(s.get("a"), Some(&json!(2)));
        assert_eq!(s.recovery().skipped_stale, 2);
        assert_eq!(s.recovery().replayed, 0);
        s.set("b", json!(3)).unwrap();
        drop(s);
        let s = Store::open(&t.0).unwrap();
        assert_eq!(s.get("b"), Some(&json!(3)));
    }

    #[test]
    fn corrupt_snapshot_is_an_error_not_a_reset() {
        let t = TempDir::new("store-snap");
        fs::write(t.0.join("snapshot.json"), b"{ not json").unwrap();
        assert!(matches!(Store::open(&t.0), Err(CoreError::Corrupt(_))));
    }

    #[test]
    fn rejects_bad_keys() {
        let t = TempDir::new("store-key");
        let mut s = Store::open(&t.0).unwrap();
        assert!(s.set("", json!(1)).is_err());
        assert!(s.set("has space", json!(1)).is_err());
    }
}
