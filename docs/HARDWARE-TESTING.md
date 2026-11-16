# Testing CIDINT OS on a real computer

Everything in [STATUS.md](STATUS.md) marked "tested" was tested in QEMU
virtual machines. This guide is how real-hardware evidence is collected.
Only someone with the computer in front of them can do it.

## What you need

- The live ISO: `cidint-os-1.0.0-desktop-live-x86_64.iso` (desktop) or
  `cidint-os-1.0.0-live-x86_64.iso` (console), and its line in `SHA256SUMS`.
- A USB stick of at least 2 GB. **Everything on it is erased.**
- Optionally a second USB stick (FAT) to save the report on. The live
  system keeps nothing, so a report saved only inside it is lost at shutdown.

## 1. Check the download

Windows PowerShell:

    Get-FileHash .\cidint-os-1.0.0-desktop-live-x86_64.iso -Algorithm SHA256

Compare with the ISO's line in `SHA256SUMS`. Do not continue if they differ.

## 2. Write the ISO to the USB stick

Use a tool that writes the image as it is, block by block:

- balenaEtcher (Windows, macOS, Linux): select the ISO, the stick, Flash.
- Rufus (Windows): select the ISO; if asked, choose **DD image mode**.
- Linux: `sudo dd if=IMAGE.iso of=/dev/sdX bs=4M conv=fsync status=progress`
  where `/dev/sdX` is the stick, never a disk (check with `lsblk` first).

Double-check the target drive in every tool: writing to the wrong drive
destroys its contents.

## 3. Start from the stick

Plug it in, open the computer's boot menu (often F12, F11, F9, Esc or
F8 at power-on) and choose the USB stick. With UEFI, Secure Boot must be
off: CIDINT OS 1.0.0 is not signed for Secure Boot. Computers with legacy
BIOS (or UEFI "CSM" mode) can also start it.

The live system does not touch the computer's disks.

## 4. Record the report

On the Desktop Edition open a terminal from the panel; on the console
edition you are already at a root prompt. Then:

    cidint-hwcheck

To keep the report, plug in the second stick and save it there:

    lsblk                              # find the stick, e.g. sdb1
    mkdir -p /mnt/stick && mount /dev/sdb1 /mnt/stick
    cidint-hwcheck --out /mnt/stick
    umount /mnt/stick

The report lists the firmware, every device and the driver the kernel
bound to it (`NO DRIVER` = not supported yet), network links, graphics
outputs, disks and failed services. It contains the computer's model and
network MAC addresses; read it before sharing it.

## 5. Note what you saw

Add to the report, in your own words:

- Did the boot menu show the stick, and did it start? (UEFI or BIOS?)
- Desktop: did the screen show the desktop? Mouse, keyboard, touchpad?
- Network: cable or Wi-Fi connected? (`ip addr`)
- Sound, sleep, brightness keys: not supported in 1.0.0; note anything odd.

## 6. Installing (optional, erases a disk)

`cidint-install` erases the whole disk you name. Only install to a disk
that holds nothing you need, or to a spare computer. It shows what is on
the disk and refuses a disk with data unless you add `--erase-existing`
and type the device path. Installed systems start with UEFI and with
legacy BIOS.

## Recording results

A hardware result counts only with the report file and the notes above.
It goes into [TESTING.md](TESTING.md) with the computer's model; nothing
is claimed for other models.
