#!/usr/bin/env python3
"""Boot the CIDINT image in QEMU and test the local AI engine: the included
model, sandbox properties, a chat answer from the included model, and
inference with a model the user adds.

    python3 tests/qemu/ai_test.py IMAGE.wic --model FILE.gguf --ovmf-code F --ovmf-vars F

The image is copied first; the build output is never modified. Exit code
0 only if every check passed.

--model is a test fixture supplied by the caller (CI downloads a tiny
story model of about 1 MB) that stands for a model the user adds. The
included model is SmolLM2-135M-Instruct. The chat check records the
answer; it shows that the engine answers with the model's chat template,
not that the answer is right.
"""
import argparse
import functools
import http.server
import json
import os
import re
import shutil
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from persistence_boot_test import Vm, sparse_copy, system_state  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--ovmf-code", type=Path, required=True)
    ap.add_argument("--ovmf-vars", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, default=Path("artifacts/qemu-test/ai"))
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    disk = args.workdir / "disk.wic"
    nvram = args.workdir / "OVMF_VARS.fd"
    sparse_copy(args.image, disk)
    shutil.copyfile(args.ovmf_vars, nvram)

    results = []

    def step(name: str, ok: bool, detail: str = ""):
        results.append({"test": name, "passed": bool(ok), "detail": str(detail)[-2500:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""),
              flush=True)

    # Serve the model file to the guest over QEMU's user-mode network.
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(args.model.resolve().parent))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    error = None
    log_path = args.workdir / "ai-serial.log"
    with open(log_path, "w", encoding="utf-8") as log:
        vm = None
        try:
            vm = Vm(disk, args.ovmf_code, nvram, log, nic=["-nic", "user,model=e1000"])
            vm.login()
            step("boot: image reaches a login prompt", True)

            bundled = "SmolLM2-135M-Instruct-Q8_0.gguf"
            rc, out = vm.run("cidint-ai status")
            step("engine is installed; the included model is the default",
                 rc == 0 and "(installed)" in out and re.search(r"models\s*: 1", out) is not None
                 and bundled in out, out)
            rc, out = vm.run(f"sha256sum /usr/share/cidint/ai/models/{bundled} | cut -d' ' -f1; "
                             f"grep -c 'Apache License 2.0' /usr/share/cidint/ai/models/{bundled}.NOTICE")
            step("the included model matches its pinned checksum and carries its licence notice",
                 out.split() == ["5a1395716f7913741cc51d98581b9b1228d80987a9f7d3664106742eb06bba83", "1"], out)
            rc, out = vm.run("cidint-ai ask -m missing.gguf 'hello'")
            step("asking for a model that is not installed is refused with a clear message (exit 3)",
                 rc == 3, f"rc={rc} {out}")

            rc, out = vm.run("cidint-ai sandbox-check", timeout=120)
            facts = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
            step("sandbox: runs as the unprivileged user 'cidint-ai'", facts.get("user") == "cidint-ai", out)
            step("sandbox: no network interface except loopback", facts.get("interfaces", "").split() == ["lo"], out)
            step("sandbox: cannot write to the data volume or the system",
                 facts.get("write_data") == "denied" and facts.get("write_system") == "denied", out)
            step("sandbox: cannot read user data, can read the model directory",
                 facts.get("read_home") == "denied" and facts.get("read_models") == "allowed", out)

            question = "What is the capital of France? Answer with one word."
            rc, answer = vm.run(f"cidint-ai chat -n 48 '{question}'", timeout=900)
            words = re.findall(r"[A-Za-z]{2,}", answer)
            step("chat: the included model answers a question (chat template, no raw markup)",
                 rc == 0 and len(words) >= 1 and "<|im_" not in answer and "[end of text]" not in answer,
                 f"rc={rc} answer={answer!r}")
            print(f"       question: {question}\n       answer:   {answer[:300]!r}", flush=True)
            # Recorded, not a pass condition: a 135M-parameter model can be wrong.
            results[-1]["answer"] = answer[:1000]
            results[-1]["answer_names_paris"] = "paris" in answer.lower()

            out = ""
            for _ in range(20):
                rc, out = vm.run("ip -4 -o addr show scope global")
                if "10.0.2.15" in out:
                    break
                vm.run("sleep 3")
            rc, out = vm.run(f"wget -q -O /data/apps/ai/models/test.gguf http://10.0.2.2:{port}/{args.model.name} "
                             "&& stat -c %s /data/apps/ai/models/test.gguf", timeout=300)
            step("a model file is installed into the model directory",
                 rc == 0 and out.strip() == str(args.model.stat().st_size), out)

            rc, text = vm.run("cidint-ai ask -m test.gguf -n 48 -s 7 'Once upon a time'", timeout=900)
            words = re.findall(r"[A-Za-z]{2,}", text)
            step("inference: the engine generates text from a model the user added", rc == 0 and len(words) >= 8,
                 f"rc={rc} output={text!r}")
            print(f"       generated: {text[:200]!r}", flush=True)

            ok, out = system_state(vm)
            step("no failed units", ok, out)

            vm.send("systemctl poweroff")
            vm.wait_exit(180)
        except Exception as e:  # report, do not hide
            error = f"{type(e).__name__}: {e}"
            step("test run completed", False, error)
        finally:
            if vm and vm.proc.poll() is None:
                vm.kill()
            httpd.shutdown()

    passed = all(r["passed"] for r in results)
    summary = {"image": str(args.image), "model": args.model.name, "kvm": os.access("/dev/kvm", os.W_OK),
               "passed": passed, "results": results, "error": error}
    (args.workdir / "ai-report.json").write_text(json.dumps(summary, indent=2))
    if not passed:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\r", "", raw)
        lines = [ln for ln in clean.splitlines() if ln.strip()]
        print("--- serial console, last 30 lines ---")
        for ln in lines[-30:]:
            print(ln[:300])
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} checks passed")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
