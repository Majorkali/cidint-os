#!/bin/sh
# make-keys.sh - create the signing keys of a CIDINT OS build.
#
#   scripts/make-keys.sh [--dev] DIR
#
# Writes into DIR (created, mode 700; it must be OUTSIDE this repository):
#   apps.key / apps.pub        Ed25519, signs software-store catalogues
#   update.key / update.pub    Ed25519, signs system updates
#   boot/db.key db.crt db.cer  RSA-2048 UEFI Secure Boot key and certificate
#   boot/PK.* boot/KEK.*       platform and key-exchange keys (only needed to
#                              set up test firmware or firmware in setup mode)
#   boot/gnupg boot/boot.gpg   RSA-4096 GPG key GRUB checks kernels and the
#                              boot menu with (private half in gnupg/)
#
# Private keys never leave DIR. Keep release keys offline and backed up;
# anyone who has them can sign software, updates and boot files that
# CIDINT OS machines will accept. --dev marks the keys as throwaway
# development keys (CI makes a fresh set per run).
#
# Needs openssl and gpg. Exit codes: 0 ok, 1 failure, 2 usage.
set -eu
DEV=0
[ "${1:-}" = "--dev" ] && { DEV=1; shift; }
[ $# -eq 1 ] || { echo "Usage: $0 [--dev] DIR" >&2; exit 2; }
DIR=$1
REPO=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$DIR"
DIR=$(cd "$DIR" && pwd)
case "$DIR/" in "$REPO"/*) echo "$0: refusing to write keys inside the repository ($REPO)" >&2; exit 1 ;; esac
[ ! -e "$DIR/apps.key" ] || { echo "$0: $DIR already holds keys; not overwriting them" >&2; exit 1; }
umask 077
chmod 700 "$DIR"
if [ $DEV -eq 1 ]; then KIND="development key, throwaway"; DAYS=30; else KIND="release key"; DAYS=3650; fi

for k in apps update; do
    openssl genpkey -algorithm ed25519 -out "$DIR/$k.key"
    openssl pkey -in "$DIR/$k.key" -pubout -out "$DIR/$k.pub"
done

mkdir -p "$DIR/boot/gnupg"
chmod 700 "$DIR/boot/gnupg"
for k in PK KEK db; do
    openssl req -new -x509 -newkey rsa:2048 -nodes -sha256 -days $DAYS \
        -subj "/CN=CIDINT OS Secure Boot $k ($KIND)/" -keyout "$DIR/boot/$k.key" -out "$DIR/boot/$k.crt" 2>/dev/null
    openssl x509 -in "$DIR/boot/$k.crt" -outform DER -out "$DIR/boot/$k.cer"
done
gpg --homedir "$DIR/boot/gnupg" --batch --pinentry-mode loopback --passphrase '' \
    --quick-gen-key "CIDINT OS boot files ($KIND)" rsa4096 sign "$( [ $DEV -eq 1 ] && echo 30d || echo 10y)" 2>/dev/null
gpg --homedir "$DIR/boot/gnupg" --batch --export > "$DIR/boot/boot.gpg"
[ -s "$DIR/boot/boot.gpg" ] || { echo "$0: the GPG boot key was not created" >&2; exit 1; }
chmod 644 "$DIR"/*.pub "$DIR/boot"/*.crt "$DIR/boot"/*.cer "$DIR/boot/boot.gpg"

ID=$(cat "$DIR/boot/db.crt" "$DIR/boot/boot.gpg" | sha256sum | cut -c1-16)
cat <<EOF
Keys written to $DIR ($KIND).

Build settings (environment of 'kas build'):
  CIDINT_APPS_PUBKEY=$(sed -n 2p "$DIR/apps.pub")
  CIDINT_UPDATE_PUBKEY=$(sed -n 2p "$DIR/update.pub")
  CIDINT_SB_KEY_DIR=$DIR/boot
  CIDINT_SB_KEY_ID=$ID

To publish the app catalogue from GitHub (workflow "Publish apps"):
  gh secret set CIDINT_APPS_SIGNING_KEY < $DIR/apps.key
  gh variable set CIDINT_APPS_RELEASE_PUBKEY --body "$(sed -n 2p "$DIR/apps.pub")"
Secure Boot: enroll $DIR/boot/db.cer in the computer's firmware (see docs/SECURE-BOOT.md).
EOF
