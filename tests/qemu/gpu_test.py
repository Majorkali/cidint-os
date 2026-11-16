#!/usr/bin/env python3
"""OpenGL through a virtual graphics processor (virtio-gpu with virgl).

    python3 tests/qemu/gpu_test.py DESKTOP.wic --ovmf-code F --ovmf-vars F

The guest gets QEMU's virtio-vga-gl device: its kernel driver (virtio_gpu)
and Mesa's virgl driver send OpenGL work to the host, which renders it
(here with the host's Mesa, through a render node). This exercises the
hardware-acceleration path of CIDINT OS (kernel DRM driver, Mesa GPU
driver, EGL/GBM, cidint-gpucheck) without a physical graphics card. It
does not prove that a particular real graphics card works.

The host needs a DRM render node (/dev/dri/renderD*); on a machine without
a graphics card the vgem module provides one. Without it the test reports
that it could not run, and fails.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from persistence_boot_test import Vm, sparse_copy, system_state  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test/gpu"))
    args = ap.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    results = []

    def step(name: str, ok: bool, detail: str = ""):
        results.append({"test": name, "passed": bool(ok), "detail": str(detail)[-2000:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""),
              flush=True)

    def finish(error=None) -> int:
        passed = bool(results) and all(r["passed"] for r in results)
        (args.workdir / "gpu-report.json").write_text(json.dumps(
            {"image": str(args.image), "kvm": os.access("/dev/kvm", os.W_OK), "passed": passed,
             "results": results, "error": error}, indent=2))
        print(f"\n{sum(r['passed'] for r in results)}/{len(results)} checks passed")
        return 0 if passed else 1

    nodes = sorted(glob.glob("/dev/dri/renderD*"))
    step("the host has a render node for the virtual graphics processor", bool(nodes),
         "no /dev/dri/renderD* on this host (load vgem or use a machine with a graphics card)")
    if not nodes:
        return finish("no render node")

    disk, nvram = args.workdir / "disk.wic", args.workdir / "OVMF_VARS.fd"
    sparse_copy(args.image, disk)
    shutil.copyfile(args.ovmf_vars, nvram)
    error = None
    with open(args.workdir / "gpu-serial.log", "w", encoding="utf-8") as log:
        vm = None
        try:
            vm = Vm(disk, args.ovmf_code, nvram, log,
                    extra=["-device", "virtio-vga-gl", "-display", f"egl-headless,rendernode={nodes[0]}"])
            vm.login()
            rc, out = vm.run("for c in /sys/class/drm/card*/device/driver; do basename $(readlink -f $c); done")
            step("the virtual graphics processor has its kernel driver (virtio_gpu)", "virtio_gpu" in out, out)
            rc, out = vm.run("su -s /bin/sh cidint -c 'cidint-gpucheck --json'", timeout=180)
            try:
                data = json.loads(out[out.index("["):])
            except ValueError:
                data = []
            gpu = next((d for d in data if d.get("driver") == "virtio_gpu"), {})
            step("OpenGL ES runs on Mesa's virgl driver, not in software",
                 "virgl" in gpu.get("renderer", "").lower() and gpu.get("kind", "").startswith("virtual"), out)
            step("a test image rendered on the graphics processor reads back correctly",
                 gpu.get("render_ok") is True, out)
            step("cidint-gpucheck reports acceleration (exit 0)", rc == 0, f"rc={rc}")
            rc, out = vm.run("su -s /bin/sh cidint -c 'cidint-gpucheck'", timeout=180)
            print(out)
            ok, st = system_state(vm)
            step("no failed units", ok, st)
            vm.send("systemctl poweroff")
            vm.wait_exit(180)
        except Exception as e:  # report, do not hide
            error = f"{type(e).__name__}: {e}"
            step("test run completed", False, error)
        finally:
            if vm and vm.proc.poll() is None:
                vm.kill()
            disk.unlink(missing_ok=True)
    return finish(error)


if __name__ == "__main__":
    sys.exit(main())
