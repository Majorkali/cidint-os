//! CIDINT Command Intelligence (CCI).
//!
//! Conventions:
//! * `--json` prints one envelope: `{"api_version","ok","data"|"error"}`.
//! * `--dry-run` reports what a changing command would do and changes nothing.
//! * Exit codes are stable; see [`exit`].
//! * Every command that changes state appends a record to the audit log.
//!
//! This release runs with the caller's own permissions. Routing privileged
//! operations through a broker service is planned (see `cidint features`).

use cidint_core::{audit, backup, features, hw, layout, marker, store::Store, CoreError};
use clap::{CommandFactory, Parser, Subcommand};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

/// Stable process exit codes.
mod exit {
    pub const OK: u8 = 0;
    pub const FAILURE: u8 = 1;
    /// Bad command line (also used by the argument parser).
    pub const USAGE: u8 = 2;
    pub const UNSUPPORTED: u8 = 3;
    pub const NOT_IMPLEMENTED: u8 = 4;
    /// An integrity or persistence check did not pass.
    pub const VERIFY_FAILED: u8 = 5;
    pub const PERMISSION: u8 = 6;
    /// Refused in order to protect existing data.
    pub const REFUSED: u8 = 7;
}

const DEFAULT_STATE_DIR: &str = "/data/state/cidint";
const DEFAULT_REPO: &str = "/data/backup/repo";

#[derive(Parser)]
#[command(
    name = "cidint",
    version,
    about = "CIDINT Command Intelligence (CCI) - CIDINT OS command line",
    after_help = "Exit codes: 0 ok, 1 failure, 2 usage, 3 unsupported here, 4 not implemented,\n5 verification failed, 6 permission denied, 7 refused to protect data."
)]
struct Cli {
    /// Machine-readable JSON output (API v1)
    #[arg(long, global = true)]
    json: bool,
    /// Show what would change without changing anything
    #[arg(long, global = true)]
    dry_run: bool,
    /// Directory holding CIDINT state (config store, audit log)
    #[arg(long, global = true, env = "CIDINT_STATE_DIR", value_name = "DIR")]
    state_dir: Option<PathBuf>,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// One-screen summary of this system
    Status,
    /// System information
    System {
        #[command(subcommand)]
        cmd: SystemCmd,
    },
    /// Hardware discovery (read-only)
    Hardware {
        #[command(subcommand)]
        cmd: HardwareCmd,
    },
    /// Is data kept across restarts, and is that proven?
    Persistence {
        #[command(subcommand)]
        cmd: PersistenceCmd,
    },
    /// Backup, verification and restore
    Storage {
        #[command(subcommand)]
        cmd: StorageCmd,
    },
    /// Durable CIDINT configuration store
    Config {
        #[command(subcommand)]
        cmd: ConfigCmd,
    },
    /// Tamper-evident audit log
    Audit {
        #[command(subcommand)]
        cmd: AuditCmd,
    },
    /// What is implemented, tested, experimental or only planned
    Features,
    /// Print a shell completion script
    Completions { shell: clap_complete::Shell },
    #[command(external_subcommand)]
    External(Vec<String>),
}

#[derive(Subcommand)]
enum SystemCmd {
    /// Version, kernel and boot information
    Info,
}

#[derive(Subcommand)]
enum HardwareCmd {
    /// CPU, memory, storage, network, TPM, virtualization
    Inventory,
}

#[derive(Subcommand)]
enum PersistenceCmd {
    /// Active mode and what each data volume is really stored on
    Status,
    /// Write a marker now; run `verify` after a restart
    Arm {
        /// Directory to test (default: the state directory)
        #[arg(long, value_name = "DIR")]
        dir: Option<PathBuf>,
    },
    /// Prove that the marker survived a real reboot
    Verify {
        #[arg(long, value_name = "DIR")]
        dir: Option<PathBuf>,
    },
}

#[derive(Subcommand)]
enum StorageCmd {
    /// Back up a directory as a new snapshot (never overwrites)
    Backup {
        source: PathBuf,
        #[arg(long, value_name = "DIR", default_value = DEFAULT_REPO)]
        repo: PathBuf,
    },
    /// Restore a snapshot into an empty location and verify every file
    Restore {
        id: String,
        dest: PathBuf,
        #[arg(long, value_name = "DIR", default_value = DEFAULT_REPO)]
        repo: PathBuf,
    },
    /// Re-hash all stored content of a snapshot
    Verify {
        id: String,
        #[arg(long, value_name = "DIR", default_value = DEFAULT_REPO)]
        repo: PathBuf,
    },
    /// List snapshots, oldest first
    List {
        #[arg(long, value_name = "DIR", default_value = DEFAULT_REPO)]
        repo: PathBuf,
    },
}

#[derive(Subcommand)]
enum ConfigCmd {
    Get {
        key: String,
    },
    /// Set a key; VALUE is parsed as JSON, otherwise stored as text
    Set {
        key: String,
        value: String,
    },
    Delete {
        key: String,
    },
    List,
    /// Fold the write-ahead log into a fresh snapshot
    Checkpoint,
}

#[derive(Subcommand)]
enum AuditCmd {
    /// Check the hash chain of the audit log
    Verify,
    /// Export verified audit records as JSON
    Export {
        /// Write to this new file instead of standard output
        #[arg(long, value_name = "FILE")]
        output: Option<PathBuf>,
    },
}

struct Failure {
    code: u8,
    message: String,
}

impl Failure {
    fn new(code: u8, message: impl Into<String>) -> Self {
        Failure {
            code,
            message: message.into(),
        }
    }
}

impl From<CoreError> for Failure {
    fn from(e: CoreError) -> Self {
        let code = match &e {
            CoreError::Io(io) if io.kind() == std::io::ErrorKind::PermissionDenied => {
                exit::PERMISSION
            }
            CoreError::Io(_) => exit::FAILURE,
            CoreError::Corrupt(_) => exit::VERIFY_FAILED,
            CoreError::Refused(_) => exit::REFUSED,
            CoreError::Unsupported(_) => exit::UNSUPPORTED,
            CoreError::Invalid(_) => exit::USAGE,
        };
        Failure::new(code, e.to_string())
    }
}

impl From<std::io::Error> for Failure {
    fn from(e: std::io::Error) -> Self {
        CoreError::Io(e).into()
    }
}

/// Result data plus the exit code (a completed check can still be a "no").
type Outcome = Result<(Value, u8), Failure>;

struct Ctx {
    dry_run: bool,
    state_dir: PathBuf,
}

impl Ctx {
    fn audit_path(&self) -> PathBuf {
        self.state_dir.join("audit.log")
    }

    fn config_dir(&self) -> PathBuf {
        self.state_dir.join("config")
    }

    fn record(&self, action: &str, detail: Value) -> Result<(), Failure> {
        let actor = std::env::var("USER")
            .or_else(|_| std::env::var("USERNAME"))
            .unwrap_or_else(|_| "unknown".into());
        audit::append(&self.audit_path(), &actor, action, detail).map_err(|e| {
            Failure::new(
                exit::FAILURE,
                format!("the change was made but could not be written to the audit log: {e}"),
            )
        })?;
        Ok(())
    }

    fn would(&self, action: &str, detail: Value) -> Outcome {
        Ok((
            json!({"dry_run": true, "would": action, "detail": detail}),
            exit::OK,
        ))
    }
}

fn linux_file(path: &str) -> Result<String, Failure> {
    fs::read_to_string(path).map_err(|_| {
        Failure::new(
            exit::UNSUPPORTED,
            format!("{path} is not readable; this command needs a running Linux system"),
        )
    })
}

fn persistence_report() -> Result<layout::Report, Failure> {
    Ok(layout::report(
        &linux_file("/proc/cmdline")?,
        &linux_file("/proc/mounts")?,
    ))
}

fn run(cmd: Cmd, ctx: &Ctx) -> Outcome {
    match cmd {
        Cmd::Status => {
            let persistence = match persistence_report() {
                Ok(r) => json!({
                    "mode": r.mode,
                    "description": r.mode_description,
                    "consistent": r.consistent,
                    "problems": r.problems,
                }),
                Err(f) => json!({"mode": null, "note": f.message}),
            };
            let audit_state = match audit::read_verified(&ctx.audit_path()) {
                Ok(entries) => json!({"entries": entries.len(), "chain_valid": true}),
                Err(e) => json!({"chain_valid": false, "error": e.to_string()}),
            };
            Ok((
                json!({
                    "product": "CIDINT OS",
                    "cli_version": cidint_core::VERSION,
                    "release_stage": "development (not production-ready)",
                    "platform": format!("{}/{}", std::env::consts::OS, std::env::consts::ARCH),
                    "persistence": persistence,
                    "state_dir": ctx.state_dir.display().to_string(),
                    "audit": audit_state,
                }),
                exit::OK,
            ))
        }
        Cmd::System {
            cmd: SystemCmd::Info,
        } => {
            let os_release = fs::read_to_string("/etc/os-release").ok();
            let pretty = os_release.as_deref().and_then(|t| {
                t.lines()
                    .find_map(|l| l.strip_prefix("PRETTY_NAME="))
                    .map(|v| v.trim_matches('"').to_string())
            });
            Ok((
                json!({
                    "cli_version": cidint_core::VERSION,
                    "api_version": cidint_core::API_VERSION,
                    "os": std::env::consts::OS,
                    "arch": std::env::consts::ARCH,
                    "os_release": pretty,
                    "kernel": fs::read_to_string("/proc/sys/kernel/osrelease").ok().map(|s| s.trim().to_string()),
                    "boot_id": marker::current_boot_id().ok(),
                }),
                exit::OK,
            ))
        }
        Cmd::Hardware {
            cmd: HardwareCmd::Inventory,
        } => Ok((
            serde_json::to_value(hw::inventory()?).expect("inventory serializes"),
            exit::OK,
        )),
        Cmd::Persistence { cmd } => persistence(cmd, ctx),
        Cmd::Storage { cmd } => storage(cmd, ctx),
        Cmd::Config { cmd } => config(cmd, ctx),
        Cmd::Audit { cmd } => audit_cmd(cmd, ctx),
        Cmd::Features => Ok((
            json!({
                "features": features::FEATURES.iter().map(|f| json!({
                    "area": f.area,
                    "name": f.name,
                    "status": f.status,
                    "status_label": f.status.label(),
                    "note": f.note,
                })).collect::<Vec<_>>(),
            }),
            exit::OK,
        )),
        Cmd::Completions { .. } => unreachable!("handled in main"),
        Cmd::External(args) => {
            let name = args.first().map(String::as_str).unwrap_or("");
            if features::PLANNED_COMMANDS.contains(&name) {
                Err(Failure::new(
                    exit::NOT_IMPLEMENTED,
                    format!("'cidint {name}' is planned but not implemented in this release; run 'cidint features'"),
                ))
            } else {
                Err(Failure::new(
                    exit::USAGE,
                    format!("unknown command '{name}'; run 'cidint --help'"),
                ))
            }
        }
    }
}

fn persistence(cmd: PersistenceCmd, ctx: &Ctx) -> Outcome {
    match cmd {
        PersistenceCmd::Status => {
            let r = persistence_report()?;
            let code = if r.consistent {
                exit::OK
            } else {
                exit::VERIFY_FAILED
            };
            Ok((serde_json::to_value(r).expect("report serializes"), code))
        }
        PersistenceCmd::Arm { dir } => {
            let dir = dir.unwrap_or_else(|| ctx.state_dir.clone());
            let boot_id = marker::current_boot_id()?;
            if ctx.dry_run {
                return ctx.would("persistence.arm", json!({"dir": dir.display().to_string()}));
            }
            let m = marker::arm(&dir, &boot_id)?;
            ctx.record("persistence.arm", json!({"dir": dir.display().to_string()}))?;
            Ok((
                json!({
                    "armed": true,
                    "marker": marker::path_in(&dir).display().to_string(),
                    "boot_id": m.boot_id,
                    "next_step": "restart the machine, then run: cidint persistence verify",
                }),
                exit::OK,
            ))
        }
        PersistenceCmd::Verify { dir } => {
            let dir = dir.unwrap_or_else(|| ctx.state_dir.clone());
            let boot_id = marker::current_boot_id()?;
            let outcome = marker::check(&dir, &boot_id)?;
            let (code, meaning) = match &outcome {
                marker::Outcome::Survived { .. } => (
                    exit::OK,
                    "PASS: the marker was written in an earlier boot and is intact",
                ),
                marker::Outcome::SameBoot { .. } => (
                    exit::VERIFY_FAILED,
                    "NOT PROVEN: no restart has happened since the marker was written",
                ),
                marker::Outcome::Missing => (
                    exit::VERIFY_FAILED,
                    "FAIL: no marker found; it was never armed or this storage does not persist",
                ),
            };
            let mut v = serde_json::to_value(&outcome).expect("outcome serializes");
            v["meaning"] = json!(meaning);
            v["dir"] = json!(dir.display().to_string());
            Ok((v, code))
        }
    }
}

fn storage(cmd: StorageCmd, ctx: &Ctx) -> Outcome {
    match cmd {
        StorageCmd::Backup { source, repo } => {
            let detail =
                json!({"source": source.display().to_string(), "repo": repo.display().to_string()});
            if ctx.dry_run {
                return ctx.would("storage.backup", detail);
            }
            let m = backup::create(&repo, &source)?;
            let report = backup::verify(&repo, &m.id)?;
            ctx.record(
                "storage.backup",
                json!({"id": m.id, "source": detail["source"], "repo": detail["repo"]}),
            )?;
            Ok((
                json!({
                    "id": m.id,
                    "files": m.files.len(),
                    "total_bytes": m.total_bytes,
                    "new_bytes_stored": m.new_bytes,
                    "skipped": m.skipped,
                    "stored_content_verified": report.files_checked,
                    "note": "Stored content was re-read and matches. Run a test restore before relying on this backup.",
                }),
                exit::OK,
            ))
        }
        StorageCmd::Restore { id, dest, repo } => {
            let detail = json!({"id": id, "dest": dest.display().to_string(), "repo": repo.display().to_string()});
            if ctx.dry_run {
                let m = backup::load(&repo, &id)?;
                return ctx.would(
                    "storage.restore",
                    json!({"id": id, "dest": detail["dest"], "files": m.files.len(), "bytes": m.total_bytes}),
                );
            }
            let r = backup::restore(&repo, &id, &dest)?;
            ctx.record("storage.restore", detail.clone())?;
            Ok((
                json!({
                    "restored": true,
                    "id": id,
                    "dest": detail["dest"],
                    "files_restored_and_verified": r.files_checked,
                    "bytes": r.bytes_checked,
                }),
                exit::OK,
            ))
        }
        StorageCmd::Verify { id, repo } => {
            let r = backup::verify(&repo, &id)?;
            Ok((
                serde_json::to_value(r).expect("report serializes"),
                exit::OK,
            ))
        }
        StorageCmd::List { repo } => {
            let mut snapshots = Vec::new();
            for id in backup::list(&repo)? {
                let m = backup::load(&repo, &id)?;
                snapshots.push(json!({
                    "id": m.id, "created": m.created, "source": m.source,
                    "files": m.files.len(), "total_bytes": m.total_bytes,
                }));
            }
            Ok((
                json!({"repo": repo.display().to_string(), "snapshots": snapshots}),
                exit::OK,
            ))
        }
    }
}

fn config(cmd: ConfigCmd, ctx: &Ctx) -> Outcome {
    match cmd {
        ConfigCmd::Get { key } => {
            let store = Store::open(&ctx.config_dir())?;
            match store.get(&key) {
                Some(v) => Ok((json!({"key": key, "value": v}), exit::OK)),
                None => Err(Failure::new(
                    exit::FAILURE,
                    format!("key '{key}' is not set"),
                )),
            }
        }
        ConfigCmd::List => {
            let store = Store::open(&ctx.config_dir())?;
            Ok((
                json!({"entries": store.entries(), "sequence": store.seq(), "recovery": store.recovery()}),
                exit::OK,
            ))
        }
        ConfigCmd::Set { key, value } => {
            let value: Value = serde_json::from_str(&value).unwrap_or(Value::String(value));
            if ctx.dry_run {
                return ctx.would("config.set", json!({"key": key, "value": value}));
            }
            let mut store = Store::open(&ctx.config_dir())?;
            store.set(&key, value.clone())?;
            ctx.record("config.set", json!({"key": key}))?;
            Ok((
                json!({"key": key, "value": value, "durable": true}),
                exit::OK,
            ))
        }
        ConfigCmd::Delete { key } => {
            if ctx.dry_run {
                return ctx.would("config.delete", json!({"key": key}));
            }
            let mut store = Store::open(&ctx.config_dir())?;
            let existed = store.delete(&key)?;
            if existed {
                ctx.record("config.delete", json!({"key": key}))?;
            }
            Ok((json!({"key": key, "deleted": existed}), exit::OK))
        }
        ConfigCmd::Checkpoint => {
            if ctx.dry_run {
                return ctx.would("config.checkpoint", json!({}));
            }
            let mut store = Store::open(&ctx.config_dir())?;
            store.checkpoint()?;
            ctx.record("config.checkpoint", json!({"sequence": store.seq()}))?;
            Ok((
                json!({"checkpointed": true, "sequence": store.seq()}),
                exit::OK,
            ))
        }
    }
}

fn audit_cmd(cmd: AuditCmd, ctx: &Ctx) -> Outcome {
    let path = ctx.audit_path();
    match cmd {
        AuditCmd::Verify => {
            let entries = audit::read_verified(&path)?;
            Ok((
                json!({"log": path.display().to_string(), "entries": entries.len(), "chain_valid": true}),
                exit::OK,
            ))
        }
        AuditCmd::Export { output } => {
            let entries = audit::read_verified(&path)?;
            let doc = json!({"log": path.display().to_string(), "entries": entries});
            match output {
                None => Ok((doc, exit::OK)),
                Some(file) => {
                    if ctx.dry_run {
                        return ctx.would(
                            "audit.export",
                            json!({"output": file.display().to_string()}),
                        );
                    }
                    write_new(&file, &serde_json::to_vec_pretty(&doc).expect("serializes"))?;
                    Ok((
                        json!({"exported": entries.len(), "output": file.display().to_string()}),
                        exit::OK,
                    ))
                }
            }
        }
    }
}

/// Create `path`; fail rather than overwrite.
fn write_new(path: &Path, data: &[u8]) -> Result<(), Failure> {
    use std::io::Write;
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| {
            if e.kind() == std::io::ErrorKind::AlreadyExists {
                Failure::new(exit::REFUSED, format!("{} already exists", path.display()))
            } else {
                e.into()
            }
        })?;
    f.write_all(data)?;
    f.sync_all()?;
    Ok(())
}

fn scalar(v: &Value) -> Option<String> {
    match v {
        Value::Null => Some("-".into()),
        Value::Bool(b) => Some(if *b { "yes" } else { "no" }.into()),
        Value::Number(n) => Some(n.to_string()),
        Value::String(s) => Some(s.clone()),
        Value::Array(a) if a.is_empty() => Some("(none)".into()),
        Value::Object(o) if o.is_empty() => Some("(none)".into()),
        _ => None,
    }
}

/// Plain-text rendering of a result for people.
fn render(v: &Value, indent: usize, out: &mut String) {
    let pad = " ".repeat(indent);
    match v {
        Value::Object(map) => {
            for (k, val) in map {
                let label = k.replace('_', " ");
                match scalar(val) {
                    Some(s) => out.push_str(&format!("{pad}{label}: {s}\n")),
                    None => {
                        out.push_str(&format!("{pad}{label}:\n"));
                        render(val, indent + 2, out);
                    }
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                match scalar(item) {
                    Some(s) => out.push_str(&format!("{pad}- {s}\n")),
                    None => {
                        out.push_str(&format!("{pad}-\n"));
                        render(item, indent + 2, out);
                    }
                }
            }
        }
        other => out.push_str(&format!("{pad}{}\n", scalar(other).unwrap_or_default())),
    }
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    if let Cmd::Completions { shell } = cli.cmd {
        clap_complete::generate(shell, &mut Cli::command(), "cidint", &mut std::io::stdout());
        return ExitCode::from(exit::OK);
    }
    let ctx = Ctx {
        dry_run: cli.dry_run,
        state_dir: cli
            .state_dir
            .unwrap_or_else(|| PathBuf::from(DEFAULT_STATE_DIR)),
    };
    match run(cli.cmd, &ctx) {
        Ok((data, code)) => {
            if cli.json {
                let envelope = json!({
                    "api_version": cidint_core::API_VERSION,
                    "ok": code == exit::OK,
                    "data": data,
                });
                println!(
                    "{}",
                    serde_json::to_string_pretty(&envelope).expect("serializes")
                );
            } else {
                let mut text = String::new();
                render(&data, 0, &mut text);
                print!("{text}");
            }
            ExitCode::from(code)
        }
        Err(f) => {
            if cli.json {
                let envelope = json!({
                    "api_version": cidint_core::API_VERSION,
                    "ok": false,
                    "error": {"code": f.code, "message": f.message},
                });
                println!(
                    "{}",
                    serde_json::to_string_pretty(&envelope).expect("serializes")
                );
            } else {
                eprintln!("cidint: {}", f.message);
            }
            ExitCode::from(f.code)
        }
    }
}
