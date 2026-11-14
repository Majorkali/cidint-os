//! Versioned, content-addressed backup repository.
//!
//! ```text
//! repo/objects/ab/cdef...   file contents, named by their SHA-256
//! repo/snapshots/<id>.json  manifest: every path with size and hash
//! ```
//!
//! Identical content is stored once, so repeated backups are incremental.
//! A snapshot is never overwritten, and a restore never overwrites an
//! existing file. A backup counts as good only after [`verify`] or a
//! [`restore`] (which re-hashes every restored file) succeeds.
//!
//! Not yet implemented: encryption of the repository itself (place the
//! repository on an encrypted volume), symlinks and special files
//! (recorded as skipped), ownership and extended attributes.

use crate::atomic::sync_dir;
use crate::{hex, unix_now, CoreError, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct FileEntry {
    /// Path relative to the backup source, `/`-separated.
    pub path: String,
    pub size: u64,
    pub sha256: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mode: Option<u32>,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct Manifest {
    pub id: String,
    pub created: u64,
    pub source: String,
    pub files: Vec<FileEntry>,
    pub empty_dirs: Vec<String>,
    /// Entries that were not backed up (symlinks, devices, sockets).
    pub skipped: Vec<String>,
    pub total_bytes: u64,
    /// Bytes newly written to the repository by this snapshot.
    pub new_bytes: u64,
}

#[derive(Serialize, Debug, Clone, PartialEq)]
pub struct VerifyReport {
    pub id: String,
    pub files_checked: u64,
    pub bytes_checked: u64,
}

fn object_path(repo: &Path, sha: &str) -> PathBuf {
    repo.join("objects").join(&sha[..2]).join(&sha[2..])
}

fn manifest_path(repo: &Path, id: &str) -> Result<PathBuf> {
    let ok = !id.is_empty() && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-');
    if !ok {
        return Err(CoreError::Invalid(format!("snapshot id {id:?}")));
    }
    Ok(repo.join("snapshots").join(format!("{id}.json")))
}

/// Copy `src` to `dst` while hashing; `dst` must not exist. Returns (size, sha256).
fn copy_hashing(src: &Path, dst: &Path) -> Result<(u64, String)> {
    let mut input = File::open(src)?;
    let mut out = OpenOptions::new().write(true).create_new(true).open(dst)?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 64 * 1024];
    let mut size = 0u64;
    loop {
        let n = input.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
        out.write_all(&buf[..n])?;
        size += n as u64;
    }
    out.sync_all()?;
    Ok((size, hex(&hasher.finalize())))
}

fn hash_file(path: &Path) -> Result<(u64, String)> {
    let mut input = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 64 * 1024];
    let mut size = 0u64;
    loop {
        let n = input.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
        size += n as u64;
    }
    Ok((size, hex(&hasher.finalize())))
}

#[cfg(unix)]
fn file_mode(meta: &fs::Metadata) -> Option<u32> {
    use std::os::unix::fs::PermissionsExt;
    Some(meta.permissions().mode() & 0o7777)
}

#[cfg(not(unix))]
fn file_mode(_meta: &fs::Metadata) -> Option<u32> {
    None
}

#[cfg(unix)]
fn set_mode(path: &Path, mode: Option<u32>) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    if let Some(m) = mode {
        fs::set_permissions(path, fs::Permissions::from_mode(m))?;
    }
    Ok(())
}

#[cfg(not(unix))]
fn set_mode(_path: &Path, _mode: Option<u32>) -> Result<()> {
    Ok(())
}

struct Walk<'a> {
    repo: &'a Path,
    tmp_dir: PathBuf,
    manifest: Manifest,
}

impl Walk<'_> {
    fn dir(&mut self, abs: &Path, rel: &str) -> Result<()> {
        let mut entries: Vec<_> = fs::read_dir(abs)?.collect::<std::io::Result<_>>()?;
        entries.sort_by_key(|e| e.file_name());
        if entries.is_empty() && !rel.is_empty() {
            self.manifest.empty_dirs.push(rel.to_string());
        }
        for e in entries {
            let name = e.file_name();
            let name = name.to_str().ok_or_else(|| {
                CoreError::Invalid(format!("non-UTF-8 file name under {}", abs.display()))
            })?;
            let child_rel = if rel.is_empty() {
                name.to_string()
            } else {
                format!("{rel}/{name}")
            };
            let meta = fs::symlink_metadata(e.path())?;
            let ft = meta.file_type();
            if ft.is_dir() {
                self.dir(&e.path(), &child_rel)?;
            } else if ft.is_file() {
                self.file(&e.path(), child_rel, &meta)?;
            } else {
                self.manifest.skipped.push(child_rel);
            }
        }
        Ok(())
    }

    fn file(&mut self, abs: &Path, rel: String, meta: &fs::Metadata) -> Result<()> {
        let tmp = self.tmp_dir.join("incoming");
        let _ = fs::remove_file(&tmp);
        let (size, sha) = copy_hashing(abs, &tmp)?;
        let obj = object_path(self.repo, &sha);
        if obj.exists() {
            fs::remove_file(&tmp)?;
        } else {
            let parent = obj.parent().expect("object path has a parent");
            fs::create_dir_all(parent)?;
            fs::rename(&tmp, &obj)?;
            sync_dir(parent)?;
            self.manifest.new_bytes += size;
        }
        self.manifest.total_bytes += size;
        self.manifest.files.push(FileEntry {
            path: rel,
            size,
            sha256: sha,
            mode: file_mode(meta),
        });
        Ok(())
    }
}

/// Back up the directory `source` into `repo` as a new snapshot.
pub fn create(repo: &Path, source: &Path) -> Result<Manifest> {
    if !source.is_dir() {
        return Err(CoreError::Invalid(format!(
            "{} is not a directory",
            source.display()
        )));
    }
    let src_canon = fs::canonicalize(source)?;
    fs::create_dir_all(repo.join("objects"))?;
    fs::create_dir_all(repo.join("snapshots"))?;
    let repo_canon = fs::canonicalize(repo)?;
    if repo_canon.starts_with(&src_canon) {
        return Err(CoreError::Refused(
            "the backup repository is inside the directory being backed up".into(),
        ));
    }
    let tmp_dir = repo.join(format!("tmp-{}", std::process::id()));
    fs::create_dir_all(&tmp_dir)?;

    let created = unix_now();
    let mut walk = Walk {
        repo,
        tmp_dir: tmp_dir.clone(),
        manifest: Manifest {
            id: String::new(),
            created,
            source: source.display().to_string(),
            files: Vec::new(),
            empty_dirs: Vec::new(),
            skipped: Vec::new(),
            total_bytes: 0,
            new_bytes: 0,
        },
    };
    let walked = walk.dir(source, "");
    let _ = fs::remove_dir_all(&tmp_dir);
    walked?;
    let mut manifest = walk.manifest;

    // Reserve a unique id; `create_new` guarantees nothing is overwritten.
    for n in 0..10_000u32 {
        let id = format!("{created}-{n:04}");
        let path = manifest_path(repo, &id)?;
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(mut f) => {
                manifest.id = id;
                f.write_all(&serde_json::to_vec_pretty(&manifest)?)?;
                f.sync_all()?;
                sync_dir(&repo.join("snapshots"))?;
                return Ok(manifest);
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e.into()),
        }
    }
    Err(CoreError::Refused(
        "could not allocate a snapshot id".into(),
    ))
}

pub fn load(repo: &Path, id: &str) -> Result<Manifest> {
    let path = manifest_path(repo, id)?;
    let bytes = fs::read(&path).map_err(|e| {
        if e.kind() == std::io::ErrorKind::NotFound {
            CoreError::Invalid(format!("no snapshot {id} in {}", repo.display()))
        } else {
            e.into()
        }
    })?;
    let m: Manifest = serde_json::from_slice(&bytes)?;
    if m.id != id {
        return Err(CoreError::Corrupt(format!(
            "manifest {id} claims to be {}",
            m.id
        )));
    }
    Ok(m)
}

/// Snapshot ids, oldest first.
pub fn list(repo: &Path) -> Result<Vec<String>> {
    let dir = repo.join("snapshots");
    let mut ids = Vec::new();
    let rd = match fs::read_dir(&dir) {
        Ok(rd) => rd,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(ids),
        Err(e) => return Err(e.into()),
    };
    for e in rd {
        let name = e?.file_name();
        if let Some(id) = name.to_str().and_then(|n| n.strip_suffix(".json")) {
            ids.push(id.to_string());
        }
    }
    ids.sort();
    Ok(ids)
}

/// Re-hash every object a snapshot refers to.
pub fn verify(repo: &Path, id: &str) -> Result<VerifyReport> {
    let m = load(repo, id)?;
    let mut report = VerifyReport {
        id: id.to_string(),
        files_checked: 0,
        bytes_checked: 0,
    };
    for f in &m.files {
        check_sha(&f.sha256)?;
        let obj = object_path(repo, &f.sha256);
        let (size, sha) = hash_file(&obj).map_err(|e| {
            CoreError::Corrupt(format!("{}: object {} unreadable: {e}", f.path, f.sha256))
        })?;
        if sha != f.sha256 || size != f.size {
            return Err(CoreError::Corrupt(format!(
                "{}: stored content no longer matches its recorded hash",
                f.path
            )));
        }
        report.files_checked += 1;
        report.bytes_checked += size;
    }
    Ok(report)
}

fn check_sha(sha: &str) -> Result<()> {
    if sha.len() == 64 && sha.bytes().all(|b| b.is_ascii_hexdigit()) {
        Ok(())
    } else {
        Err(CoreError::Corrupt(format!("bad object name {sha:?}")))
    }
}

/// A manifest path is untrusted input: allow only plain relative names.
fn safe_join(dest: &Path, rel: &str) -> Result<PathBuf> {
    let p = Path::new(rel);
    if rel.is_empty() || !p.components().all(|c| matches!(c, Component::Normal(_))) {
        return Err(CoreError::Corrupt(format!(
            "manifest contains unsafe path {rel:?}"
        )));
    }
    Ok(dest.join(p))
}

/// Restore snapshot `id` into `dest`. Fails before writing anything if a
/// file to be restored already exists there; every restored file is
/// re-hashed and compared with the manifest.
pub fn restore(repo: &Path, id: &str, dest: &Path) -> Result<VerifyReport> {
    let m = load(repo, id)?;
    let mut targets = Vec::with_capacity(m.files.len());
    for f in &m.files {
        check_sha(&f.sha256)?;
        let target = safe_join(dest, &f.path)?;
        if fs::symlink_metadata(&target).is_ok() {
            return Err(CoreError::Refused(format!(
                "{} already exists; restore into an empty location",
                target.display()
            )));
        }
        targets.push(target);
    }
    fs::create_dir_all(dest)?;
    for d in &m.empty_dirs {
        fs::create_dir_all(safe_join(dest, d)?)?;
    }
    let mut report = VerifyReport {
        id: id.to_string(),
        files_checked: 0,
        bytes_checked: 0,
    };
    for (f, target) in m.files.iter().zip(&targets) {
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)?;
        }
        let (size, sha) = copy_hashing(&object_path(repo, &f.sha256), target)?;
        if sha != f.sha256 || size != f.size {
            let _ = fs::remove_file(target);
            return Err(CoreError::Corrupt(format!(
                "{}: backup content is damaged; restore stopped",
                f.path
            )));
        }
        set_mode(target, f.mode)?;
        report.files_checked += 1;
        report.bytes_checked += size;
    }
    sync_dir(dest)?;
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::TempDir;

    fn sample(root: &Path) {
        fs::create_dir_all(root.join("docs/deep")).unwrap();
        fs::create_dir_all(root.join("empty")).unwrap();
        fs::write(root.join("a.txt"), b"alpha").unwrap();
        fs::write(root.join("docs/b.bin"), vec![7u8; 200_000]).unwrap();
        fs::write(root.join("docs/deep/c.txt"), b"alpha").unwrap(); // duplicate content
    }

    #[test]
    fn backup_restore_roundtrip_is_byte_identical() {
        let t = TempDir::new("backup-rt");
        let (src, repo, out) = (t.0.join("src"), t.0.join("repo"), t.0.join("out"));
        sample(&src);
        let m = create(&repo, &src).unwrap();
        assert_eq!(m.files.len(), 3);
        assert_eq!(m.total_bytes, 200_010);
        assert_eq!(m.new_bytes, 200_005, "duplicate content stored once");
        assert_eq!(m.empty_dirs, vec!["empty".to_string()]);

        assert_eq!(verify(&repo, &m.id).unwrap().files_checked, 3);
        let r = restore(&repo, &m.id, &out).unwrap();
        assert_eq!(r.bytes_checked, 200_010);
        assert_eq!(fs::read(out.join("a.txt")).unwrap(), b"alpha");
        assert_eq!(
            fs::read(out.join("docs/b.bin")).unwrap(),
            vec![7u8; 200_000]
        );
        assert_eq!(fs::read(out.join("docs/deep/c.txt")).unwrap(), b"alpha");
        assert!(out.join("empty").is_dir());
    }

    #[test]
    fn second_backup_is_incremental_and_keeps_the_first() {
        let t = TempDir::new("backup-inc");
        let (src, repo) = (t.0.join("src"), t.0.join("repo"));
        sample(&src);
        let first = create(&repo, &src).unwrap();
        fs::write(src.join("a.txt"), b"alpha v2").unwrap();
        let second = create(&repo, &src).unwrap();
        assert_ne!(first.id, second.id);
        assert_eq!(second.new_bytes, 8);
        assert_eq!(
            list(&repo).unwrap(),
            vec![first.id.clone(), second.id.clone()]
        );
        // The older version is still restorable.
        let out = t.0.join("old");
        restore(&repo, &first.id, &out).unwrap();
        assert_eq!(fs::read(out.join("a.txt")).unwrap(), b"alpha");
    }

    #[test]
    fn damaged_object_fails_verify_and_restore() {
        let t = TempDir::new("backup-dmg");
        let (src, repo) = (t.0.join("src"), t.0.join("repo"));
        sample(&src);
        let m = create(&repo, &src).unwrap();
        let victim = &m.files.iter().find(|f| f.path == "a.txt").unwrap().sha256;
        fs::write(object_path(&repo, victim), b"alphA").unwrap();
        assert!(matches!(verify(&repo, &m.id), Err(CoreError::Corrupt(_))));
        assert!(matches!(
            restore(&repo, &m.id, &t.0.join("out")),
            Err(CoreError::Corrupt(_))
        ));
    }

    #[test]
    fn restore_never_overwrites() {
        let t = TempDir::new("backup-ow");
        let (src, repo, out) = (t.0.join("src"), t.0.join("repo"), t.0.join("out"));
        sample(&src);
        let m = create(&repo, &src).unwrap();
        fs::create_dir_all(&out).unwrap();
        fs::write(out.join("a.txt"), b"precious").unwrap();
        assert!(matches!(
            restore(&repo, &m.id, &out),
            Err(CoreError::Refused(_))
        ));
        assert_eq!(fs::read(out.join("a.txt")).unwrap(), b"precious");
        assert!(
            !out.join("docs").exists(),
            "nothing may be written on refusal"
        );
    }

    #[test]
    fn hostile_manifest_paths_are_rejected() {
        let t = TempDir::new("backup-path");
        assert!(safe_join(&t.0, "../escape").is_err());
        assert!(safe_join(&t.0, "/etc/passwd").is_err());
        assert!(safe_join(&t.0, "ok/file").is_ok());
    }

    #[test]
    fn repository_inside_source_is_refused() {
        let t = TempDir::new("backup-in");
        let src = t.0.join("src");
        sample(&src);
        assert!(matches!(
            create(&src.join("repo"), &src),
            Err(CoreError::Refused(_))
        ));
    }
}
