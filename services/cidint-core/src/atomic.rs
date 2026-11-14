//! Durable, atomic file replacement.
//!
//! A reader sees either the complete old content or the complete new
//! content, never a mixture, including after power loss (as far as the
//! filesystem and the drive honour flush requests).

use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::Path;

/// Flush a directory entry change (create, rename) to stable storage.
pub fn sync_dir(dir: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        fs::File::open(dir)?.sync_all()
    }
    #[cfg(not(unix))]
    {
        // Windows has no directory fsync; NTFS journals the rename itself.
        let _ = dir;
        Ok(())
    }
}

/// Write `data` to `path` atomically: temp file, fsync, rename, fsync dir.
pub fn write_atomic(path: &Path, data: &[u8]) -> io::Result<()> {
    let dir = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let name = path
        .file_name()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "path has no file name"))?;
    let tmp = dir.join(format!(
        ".{}.tmp.{}",
        name.to_string_lossy(),
        std::process::id()
    ));
    // A leftover temp file from a crashed process with the same pid is garbage.
    let _ = fs::remove_file(&tmp);
    let result = (|| {
        let mut f = OpenOptions::new().write(true).create_new(true).open(&tmp)?;
        f.write_all(data)?;
        f.sync_all()?;
        drop(f);
        fs::rename(&tmp, path)?;
        sync_dir(dir)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::TempDir;

    #[test]
    fn replaces_existing_content_and_leaves_no_temp_file() {
        let t = TempDir::new("atomic");
        let p = t.0.join("conf.json");
        write_atomic(&p, b"one").unwrap();
        write_atomic(&p, b"two").unwrap();
        assert_eq!(fs::read(&p).unwrap(), b"two");
        let names: Vec<_> = fs::read_dir(&t.0)
            .unwrap()
            .map(|e| e.unwrap().file_name())
            .collect();
        assert_eq!(names.len(), 1, "unexpected files: {names:?}");
    }
}
