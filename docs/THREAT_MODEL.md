# Threat model

Version 0.2.0. This describes the system as it exists, not as planned.
CIDINT OS has not been security-audited. It offers no absolute protection
and holds no certification.

## Assets

1. User and server data on `/data`.
2. Integrity of the system image.
3. Integrity of CIDINT state (configuration store, audit log, backups).
4. Integrity of the build and release chain.

## Defended today

| Threat | Mechanism | Evidence |
|---|---|---|
| Power loss or crash during a CIDINT state write | write-ahead log with checksums, atomic replace | unit and CLI tests |
| Silent corruption of the state log | SHA-256 per record; damaged tail is cut and reported | unit test (bit flip) |
| Edit or removal of earlier audit records | hash chain | unit and CLI tests |
| Corrupted backup content going unnoticed | content addressing; verify; re-hash on restore | unit tests |
| Restore overwriting existing files | refusal before any write | unit and CLI tests |
| Path traversal through a crafted backup manifest | only plain relative paths accepted | unit test |
| Dependency substitution in the Rust build | `Cargo.lock` checksums; same list pinned in the Yocto recipe; CI checks they match | CI step |
| Upstream layer drift | `poky` pinned to a commit | kas file |
| Accidental writes to the system image | read-only root filesystem | to be confirmed by the QEMU test |

## Not defended today

- **Physical access / stolen disk.** No disk encryption. Anyone with the
  disk reads everything.
- **Boot-chain tampering.** No Secure Boot signing, no measured boot, no
  dm-verity. The read-only mount stops accidents, not an attacker with root
  or physical access.
- **Root compromise.** Root can remount the system read-write, rewrite the
  whole audit log, and destroy backups. No mandatory access control is
  configured.
- **Audit log truncation or wholesale replacement** by a user with write
  access. Remote log shipping is planned.
- **Malicious applications.** No sandboxing, no security domains.
- **Network attackers.** The base image ships no network policy or firewall
  rules; the QEMU test runs without a network device.
- **Anonymity.** None. No Tor, VPN or kill switch.
- **Supply chain beyond pinning.** Upstream source is trusted as fetched at
  the pinned revisions. No release signing yet. GitHub Actions used by CI
  are pinned by major version, not by commit.
- **The development image.** Empty root password. It must never be deployed.

## Trust assumptions

- The build host and CI runner are trusted.
- The hardware and firmware are trusted. Compromised firmware defeats every
  software control.
- Storage devices honour flush requests.

## Standing limits, even when planned features land

- Endpoint compromise defeats network privacy tools.
- Tor and VPNs do not prevent traffic correlation or browser fingerprinting.
- Post-quantum algorithms reduce one class of future risk; nothing is
  "quantum-proof".
- No claim of FIPS validation or any certification will be made without the
  specific independent validation.

## Reporting a vulnerability

Open a private security advisory on the GitHub repository. Do not file
public issues for vulnerabilities.
