//! End-to-end tests that run the real `cidint` binary.
//! Each invocation is a separate process, so state seen by a later
//! command has really been written to disk by an earlier one.

use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU32, Ordering};

static N: AtomicU32 = AtomicU32::new(0);

struct Sandbox(PathBuf);

impl Sandbox {
    fn new(tag: &str) -> Self {
        let p = std::env::temp_dir().join(format!(
            "cidint-cli-{tag}-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(&p).unwrap();
        Sandbox(p)
    }

    fn state(&self) -> PathBuf {
        self.0.join("state")
    }

    /// Run `cidint` and return (exit code, stdout, stderr).
    fn run(&self, args: &[&str]) -> (i32, String, String) {
        let out = Command::new(env!("CARGO_BIN_EXE_cidint"))
            .env("CIDINT_STATE_DIR", self.state())
            .args(args)
            .output()
            .expect("cidint runs");
        (
            out.status.code().expect("exit code"),
            String::from_utf8_lossy(&out.stdout).into_owned(),
            String::from_utf8_lossy(&out.stderr).into_owned(),
        )
    }

    fn json(&self, args: &[&str]) -> (i32, Value) {
        let mut full = args.to_vec();
        full.push("--json");
        let (code, stdout, stderr) = self.run(&full);
        let v = serde_json::from_str(&stdout)
            .unwrap_or_else(|e| panic!("not JSON ({e}): {stdout}\nstderr: {stderr}"));
        (code, v)
    }
}

impl Drop for Sandbox {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn s(p: &Path) -> &str {
    p.to_str().unwrap()
}

#[test]
fn config_survives_across_processes_and_is_audited() {
    let sb = Sandbox::new("config");
    assert_eq!(
        sb.run(&["config", "set", "theme/name", "obsidian-command"])
            .0,
        0
    );
    assert_eq!(sb.run(&["config", "set", "ui/scale", "1.25"]).0, 0);

    let (code, v) = sb.json(&["config", "get", "theme/name"]);
    assert_eq!(code, 0);
    assert_eq!(v["api_version"], "v1");
    assert_eq!(v["ok"], true);
    assert_eq!(v["data"]["value"], "obsidian-command");
    assert_eq!(
        sb.json(&["config", "get", "ui/scale"]).1["data"]["value"],
        1.25
    );

    assert_eq!(sb.run(&["config", "checkpoint"]).0, 0);
    assert_eq!(sb.run(&["config", "delete", "ui/scale"]).0, 0);
    assert_eq!(sb.run(&["config", "get", "ui/scale"]).0, 1);
    assert_eq!(
        sb.json(&["config", "get", "theme/name"]).1["data"]["value"],
        "obsidian-command"
    );

    let (code, v) = sb.json(&["audit", "verify"]);
    assert_eq!(code, 0);
    assert_eq!(v["data"]["entries"], 4); // set, set, checkpoint, delete
}

#[test]
fn dry_run_changes_nothing() {
    let sb = Sandbox::new("dry");
    let (code, v) = sb.json(&["config", "set", "a", "1", "--dry-run"]);
    assert_eq!(code, 0);
    assert_eq!(v["data"]["dry_run"], true);
    assert!(!sb.state().exists(), "dry run must not create state");
}

#[test]
fn interrupted_write_is_recovered_by_the_next_command() {
    let sb = Sandbox::new("torn");
    sb.run(&["config", "set", "a", "1"]);
    sb.run(&["config", "set", "b", "2"]);
    let wal = sb.state().join("config").join("wal.log");
    let len = fs::metadata(&wal).unwrap().len();
    fs::OpenOptions::new()
        .write(true)
        .open(&wal)
        .unwrap()
        .set_len(len - 5)
        .unwrap();

    let (code, v) = sb.json(&["config", "list"]);
    assert_eq!(code, 0);
    assert_eq!(v["data"]["entries"]["a"], 1);
    assert!(v["data"]["entries"].get("b").is_none());
    assert!(v["data"]["recovery"]["discarded_bytes"].as_u64().unwrap() > 0);
}

#[test]
fn backup_verify_restore_and_refusal_to_overwrite() {
    let sb = Sandbox::new("backup");
    let (src, repo, out) = (sb.0.join("src"), sb.0.join("repo"), sb.0.join("out"));
    fs::create_dir_all(src.join("sub")).unwrap();
    fs::write(src.join("report.txt"), b"quarterly numbers").unwrap();
    fs::write(src.join("sub/db.bin"), vec![42u8; 70_000]).unwrap();

    let (code, v) = sb.json(&["storage", "backup", s(&src), "--repo", s(&repo)]);
    assert_eq!(code, 0, "{v}");
    let id = v["data"]["id"].as_str().unwrap().to_string();
    assert_eq!(v["data"]["files"], 2);

    assert_eq!(sb.run(&["storage", "verify", &id, "--repo", s(&repo)]).0, 0);
    let (_, list) = sb.json(&["storage", "list", "--repo", s(&repo)]);
    assert_eq!(list["data"]["snapshots"][0]["id"], id.as_str());

    // Lose the original, restore, compare.
    fs::remove_dir_all(&src).unwrap();
    let (code, v) = sb.json(&["storage", "restore", &id, s(&out), "--repo", s(&repo)]);
    assert_eq!(code, 0, "{v}");
    assert_eq!(v["data"]["files_restored_and_verified"], 2);
    assert_eq!(
        fs::read(out.join("report.txt")).unwrap(),
        b"quarterly numbers"
    );
    assert_eq!(
        fs::read(out.join("sub/db.bin")).unwrap(),
        vec![42u8; 70_000]
    );

    // A second restore onto the same place must be refused (exit 7).
    assert_eq!(
        sb.run(&["storage", "restore", &id, s(&out), "--repo", s(&repo)])
            .0,
        7
    );
    // An unknown snapshot is a usage error.
    assert_eq!(
        sb.run(&["storage", "verify", "nope", "--repo", s(&repo)]).0,
        2
    );
}

#[test]
fn tampered_audit_log_fails_verification() {
    let sb = Sandbox::new("audit");
    sb.run(&["config", "set", "a", "1"]);
    sb.run(&["config", "set", "b", "2"]);
    let log = sb.state().join("audit.log");
    let text = fs::read_to_string(&log).unwrap();
    fs::write(&log, text.replacen("\"key\":\"a\"", "\"key\":\"x\"", 1)).unwrap();
    let (code, v) = sb.json(&["audit", "verify"]);
    assert_eq!(code, 5);
    assert_eq!(v["ok"], false);
}

#[test]
fn audit_export_does_not_overwrite() {
    let sb = Sandbox::new("export");
    sb.run(&["config", "set", "a", "1"]);
    let file = sb.0.join("audit.json");
    assert_eq!(sb.run(&["audit", "export", "--output", s(&file)]).0, 0);
    let doc: Value = serde_json::from_slice(&fs::read(&file).unwrap()).unwrap();
    assert_eq!(doc["entries"][0]["action"], "config.set");
    assert_eq!(sb.run(&["audit", "export", "--output", s(&file)]).0, 7);
}

#[test]
fn planned_and_unknown_commands_have_distinct_exit_codes() {
    let sb = Sandbox::new("planned");
    let (code, _, err) = sb.run(&["domain", "list"]);
    assert_eq!(code, 4);
    assert!(err.contains("planned"));
    assert_eq!(sb.run(&["frobnicate"]).0, 2);
    assert_eq!(sb.run(&["config"]).0, 2);
}

#[test]
fn informational_commands_work_everywhere() {
    let sb = Sandbox::new("info");
    let (code, v) = sb.json(&["status"]);
    assert_eq!(code, 0);
    assert_eq!(v["data"]["product"], "CIDINT OS");
    let (code, v) = sb.json(&["features"]);
    assert_eq!(code, 0);
    assert!(v["data"]["features"].as_array().unwrap().len() > 10);
    let (code, out, _) = sb.run(&["completions", "bash"]);
    assert_eq!(code, 0);
    assert!(out.contains("cidint"));
}

#[cfg(not(target_os = "linux"))]
#[test]
fn linux_only_commands_say_so_instead_of_inventing_data() {
    let sb = Sandbox::new("nolinux");
    assert_eq!(sb.run(&["hardware", "inventory"]).0, 3);
    assert_eq!(sb.run(&["persistence", "status"]).0, 3);
    assert_eq!(sb.run(&["persistence", "verify"]).0, 3);
}
