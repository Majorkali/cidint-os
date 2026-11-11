# CIDINT OS

**Cyber Intelligence, Defense, Innovation & Network Technology Operating System**
Secure. Anonymous. Intelligent. Adaptive.

CIDINT OS is a hardened, privacy-first Linux distribution: a live system that boots
from USB or ISO, forgets everything at shutdown unless you opt in to encrypted
persistence, isolates your activities in compartments, can force all traffic through
Tor, installs software from other ecosystems through one engine, and tunes itself to
the hardware it finds.

This folder is the complete source of the OS and the system that builds its ISO.

> **Status: version 0.1, source complete, ISO not yet built or boot-tested.**
> The core logic is covered by automated tests (`tests/`), but the image has not been
> assembled or run on real hardware yet. Treat the first build as a release candidate
> to test in a virtual machine, not as something to trust with your safety.

## What it is built on

CIDINT OS is a distribution on top of Debian 13 and the Linux kernel, the same approach
Tails, Qubes OS, Kali and Whonix take. A brand-new kernel could not run existing
`.deb`, `.rpm` or `.exe` software or drive real hardware, so the original work here is
the CIDINT layer:

| Component | Where | What it does |
|---|---|---|
| `cidint` CLI | `usr/bin/cidint`, `cli.py` | One command for every subsystem |
| Control Center | `control_center.py` | Graphical security dashboard, live graphs, domains, apps, assistant |
| UAEE | `uaee.py` | Universal Application Execution Engine |
| Domains | `domains.py`, `sandbox.py` | Isolated compartments, disposable environments, throwaway VMs |
| Anonymous Mode | `anon.py`, `nft-anon.nft` | Transparent Tor routing, fail-closed, kill switch |
| Hardware engine | `hwadapt.py` | Hardware inventory, load forecasting, adaptive tuning |
| Sentinel | `sentinel.py` | Behavioural threat detection and containment |
| Assistant | `assistant.py` | Offline natural-language control |

(Paths are under `live/config/includes.chroot/usr/lib/cidint/cidint/`.)

## Building the ISO

You need a Linux environment, about 25 GB of free disk, and a network connection.
The build takes 30 to 90 minutes and produces `out/cidint-os-amd64.iso` (roughly 4 to
6 GB, far below the 15 GB limit, which the build enforces).

**On Windows (WSL2):**

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

If WSL is not installed or is broken, fix it once from an Administrator PowerShell with
`wsl --install -d Debian`, reboot, and run the script again.

**On Linux (Debian or Ubuntu):**

```bash
sudo bash build.sh
```

**In the cloud:** push this folder to a GitHub repository and run the
*Build CIDINT OS ISO* workflow. It builds amd64 and arm64 images as artifacts.

## Booting

- **USB:** write the ISO with Rufus (choose *DD image mode*), balenaEtcher, or `dd`.
- **Virtual machine:** attach the ISO to VirtualBox, VMware, Hyper-V or QEMU/KVM
  with at least 4 GB of RAM and 2 CPUs.

The boot menu offers *CIDINT OS (amnesic)*, where nothing is written to disk, and
*CIDINT OS (encrypted persistence)*, which unlocks a LUKS2 volume you created with
`sudo cidint persist create /dev/sdXN --yes-destroy-data`.

## Using it

```bash
cidint status                       # security posture score and checks
cidint center                       # open the Control Center

cidint anon on                      # force all traffic through Tor
cidint lockdown on                  # kill switch: block all network traffic
cidint panic --poweroff             # emergency: block, lock, power off

cidint domain list
cidint domain run banking -- firefox-esr --no-remote
cidint domain disposable -- firefox-esr --no-remote     # destroyed on exit
cidint domain vm suspicious.iso     # throwaway virtual machine

cidint app inspect setup.exe        # what is it, will it run, is it clean
cidint app install program.AppImage # scan, isolate, add to launcher
cidint app run script.sh            # run once in a disposable sandbox

cidint hw status                    # hardware and adaptive tuning
cidint sentinel events              # behavioural detections
cidint ask "why is my system slow"  # natural-language control
```

Double-clicking a package or installer in the file manager opens the same engine.

## Feature status, honestly

**Implemented in this release**

- Live ISO, bootable from USB, amnesic by default; optional LUKS2 + Argon2id persistence
- Kernel hardening (memory zeroing, ASLR tuning, restricted kernel interfaces, module
  blacklist), AppArmor, deny-inbound firewall, compressed RAM swap only (never on disk)
- Anonymous Mode: all TCP and DNS through Tor, everything else blocked, fail-closed
- MAC address randomisation, no hostname in DHCP, no connectivity pings
- Domains with private storage and per-domain network policy; disposable compartments;
  throwaway KVM virtual machines
- UAEE: identifies files by content, hashes, malware-scans, checks architecture and
  installs or runs DEB, RPM, AppImage, Flatpak, Snap, source archives, shell scripts,
  Windows EXE/MSI/BAT (Wine), JAR, APK (Waydroid), foreign-CPU Linux binaries (QEMU)
- Hardware engine: detection, Holt forecasting, power profile / I/O scheduler / VM tuning
- Sentinel: per-program statistical baselines plus attack heuristics, optional freezing
- Offline assistant, Control Center, CIDINT Dark theme, wallpaper
- amd64 and arm64 builds

**Provided by the Debian base and included, not written here**

Wine, Tor, QEMU/KVM, Flatpak, Podman, KDE Plasma, LUKS, WireGuard, OpenVPN, FIDO2 and
TPM tooling, compilers and language toolchains, Btrfs/XFS/LVM/RAID tools.

**Not done, and what would be needed**

| Wanted | Reality |
|---|---|
| Qubes-grade isolation | Domains use kernel namespaces. A kernel exploit crosses them. True Qubes-style isolation needs a Xen-based architecture, which is a different OS design. |
| Guaranteed anonymity | Not possible for any system. Tor resists network observers, not a global adversary, a compromised endpoint, or your own mistakes. |
| "Military grade" | A marketing term. The cryptography is standard and strong (AES-256, Argon2id, X25519/Ed25519 in the base), but nothing here has been audited or certified. |
| macOS and iOS apps | Cannot run: no workable compatibility layer exists and Apple's licences forbid it. They are identified and refused with an explanation. |
| iOS / BlackBerry hardware | Locked bootloaders; no third-party OS can be installed on them. |
| RISC-V, POWER, s390x, MIPS, LoongArch images | Debian supports several of these, but live-image tooling here targets amd64 and arm64 only. |
| Signed Secure Boot chain of our own | The image relies on Debian's signed shim, GRUB and kernel. Own keys, measured boot and remote attestation are future work. |
| Local LLM assistant | The assistant is a keyword intent engine. A local model (llama.cpp) can be added behind the same interface. |
| Post-quantum cryptography | Depends on the base OpenSSL/OpenSSH versions; nothing CIDINT-specific is implemented. |
| Installer to hard disk, immutable base, A/B updates | Not implemented. This release is live-only. |
| Voice control, gestures, cloud sync, remote desktop, AI firewall | Not implemented. |
| Clipboard isolation between domains | Not enforced under Wayland compartments sharing the compositor socket. |

## Known rough edges to check on first boot

- Package names in `optional-packages.list` that Debian does not ship are skipped; the
  list of skipped ones is saved in `/usr/share/cidint/optional-missing.txt`.
- The live user has passwordless `sudo`, as on a standard Debian live image.
- Anonymous Mode redirects IPv4 only and blocks IPv6 entirely.
- The Control Center and the desktop theme have not been seen running yet.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Layout

```
build.ps1 / build.sh        build the ISO (Windows / Linux)
tools/patch-bootloaders.py  boot menu branding and persistence entry
live/auto/config            live-build configuration, kernel hardening flags
live/config/package-lists/  core packages
live/config/hooks/          image customisation at build time
live/config/includes.chroot files copied into the OS (the CIDINT layer)
tests/                      automated tests
```
