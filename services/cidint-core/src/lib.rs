//! CIDINT OS core library.
//!
//! Everything here is original CIDINT code. It deliberately has no mock
//! data paths: a function either reads the real system or returns
//! [`CoreError::Unsupported`].

pub mod atomic;
pub mod audit;
pub mod backup;
pub mod features;
pub mod hw;
pub mod layout;
pub mod marker;
pub mod store;

use std::fmt;
use std::io;

/// Version of the JSON contract emitted by the CLI and local API.
pub const API_VERSION: &str = "v1";
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Debug)]
pub enum CoreError {
    Io(io::Error),
    /// Stored data failed an integrity check.
    Corrupt(String),
    /// The operation was refused to protect existing data.
    Refused(String),
    /// The running platform cannot provide this information.
    Unsupported(String),
    Invalid(String),
}

impl fmt::Display for CoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CoreError::Io(e) => write!(f, "I/O error: {e}"),
            CoreError::Corrupt(m) => write!(f, "integrity failure: {m}"),
            CoreError::Refused(m) => write!(f, "refused: {m}"),
            CoreError::Unsupported(m) => write!(f, "unsupported on this system: {m}"),
            CoreError::Invalid(m) => write!(f, "invalid input: {m}"),
        }
    }
}

impl std::error::Error for CoreError {}

impl From<io::Error> for CoreError {
    fn from(e: io::Error) -> Self {
        CoreError::Io(e)
    }
}

impl From<serde_json::Error> for CoreError {
    fn from(e: serde_json::Error) -> Self {
        CoreError::Corrupt(format!("malformed JSON: {e}"))
    }
}

pub type Result<T> = std::result::Result<T, CoreError>;

pub fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push(DIGITS[(b >> 4) as usize] as char);
        s.push(DIGITS[(b & 0xf) as usize] as char);
    }
    s
}

pub fn unix_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

#[cfg(test)]
pub(crate) mod testutil {
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU32, Ordering};

    static N: AtomicU32 = AtomicU32::new(0);

    /// Fresh empty directory under the system temp dir, removed on drop.
    pub struct TempDir(pub PathBuf);

    impl TempDir {
        pub fn new(tag: &str) -> Self {
            let p = std::env::temp_dir().join(format!(
                "cidint-test-{tag}-{}-{}",
                std::process::id(),
                N.fetch_add(1, Ordering::SeqCst)
            ));
            let _ = std::fs::remove_dir_all(&p);
            std::fs::create_dir_all(&p).unwrap();
            TempDir(p)
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
}
