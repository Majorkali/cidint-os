# Signing keys and UEFI Secure Boot

Status: **Experimental.** Works in QEMU's firmware in the CI test
(`tests/qemu/secureboot_test.py`) once that test has run; never tried on a
real PC. The live ISOs do not start with Secure Boot on.

## What is signed

| Key | Signs | Checked by |
|---|---|---|
| `boot/db.key` (RSA-2048, UEFI) | GRUB, `EFI/BOOT/bootx64.efi` | the PC's firmware, when `db.cer` is enrolled |
| `boot/gnupg` (RSA-4096, GPG) | kernels `bzImage-a`/`-b` and `EFI/BOOT/grub.cfg` (`.sig` files) | GRUB (`check_signatures=enforce`; the public key is built into GRUB) |
| `update.key` (Ed25519) | system updates (`update.sha256.sig`) | `cidint-slot` |
| `apps.key` (Ed25519) | software catalogues (`index.json.sig`) | the software store |

GRUB is built with every module it needs and loads none from disk. The boot
menu finds the system slots itself, so one signed menu serves every
computer. The only input GRUB reads without a signature is the A/B boot
state (`EFI/BOOT/grubenv`); only the names `slot`, `trial`, `tried` and
`rolled_back` are read from it and only the values `a` and `b` are used.
With signatures enforced, extra kernel arguments from `cidint-install
--kernel-args` are ignored. Updates carry the kernel inside the signed
system image, with its signature; a computer that checks boot signatures
refuses an update without one.

Not covered: the kernel's own lockdown mode and module signing, measured
boot (TPM), and revocation (dbx). Anyone with the private keys can make
boot files these computers accept.

## Making keys

```bash
scripts/make-keys.sh ~/cidint-release-keys
```

The script refuses to write inside the repository and prints the build
settings (`CIDINT_SB_KEY_DIR`, `CIDINT_SB_KEY_ID`, `CIDINT_UPDATE_PUBKEY`,
`CIDINT_APPS_PUBKEY`). Keep the directory offline and backed up. CI makes a
throwaway set for every run (`--dev`); images from CI only accept boot
files, updates and catalogues of the same run.

The build host needs `sbsign` (package sbsigntool) and `gpg`.

## Turning Secure Boot on with the CIDINT key

1. Install CIDINT OS with Secure Boot off.
2. Run `cidint-secureboot status`. It should say `boot files : signed`.
3. Copy the certificate to a FAT-formatted USB stick:
   `cidint-secureboot export-cert /path/to/stick`.
4. In the firmware setup, find the Secure Boot key management. Add
   `cidint-db.cer` to the **db** (allowed signatures) list. Keep the
   existing keys if other systems (for example Windows) must keep starting.
   Some firmware only lets you add keys after "custom mode" or after
   clearing the keys (setup mode); clearing keys can stop other systems from
   starting, so read your firmware's manual first.
5. Turn Secure Boot on and start CIDINT OS. `cidint-secureboot status`
   should now say `secure boot : on`.

If the computer no longer starts, turn Secure Boot off again in the firmware
setup; nothing on the disk was changed by these steps.

`cidint-secureboot` never writes firmware variables.
