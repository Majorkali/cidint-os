#!/usr/bin/env python3
"""Build a signed CIDINT software source from apps/<id>/.

    python3 scripts/build-app-repo.py --key PRIVATE.pem --out DIR

Each app folder holds app.json, icon.png and its program files. Writes one
reproducible .cidapp bundle per app, index.json listing their SHA-256
checksums, and index.json.sig (Ed25519, made with openssl). The private
key is read, never copied; keep it out of this repository."""
import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def bundle(app_dir: Path) -> bytes:
    buf = io.BytesIO()
    # Fixed metadata so the same sources give the same bytes.
    import gzip
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for p in sorted(x for x in app_dir.rglob("*") if "__pycache__" not in x.parts):
                rel = p.relative_to(app_dir).as_posix()
                info = tarfile.TarInfo(rel)
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mtime = 0
                if p.is_dir():
                    info.type, info.mode = tarfile.DIRTYPE, 0o755
                    tar.addfile(info)
                else:
                    data = p.read_bytes()
                    info.size = len(data)
                    info.mode = 0o755 if rel.startswith("bin/") else 0o644
                    tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    apps = []
    for app_dir in sorted((ROOT / "apps").iterdir()):
        meta = json.loads((app_dir / "app.json").read_text(encoding="utf-8"))
        assert meta["id"] == app_dir.name, app_dir
        blob = bundle(app_dir)
        name = f"{meta['id']}-{meta['version']}.cidapp"
        (args.out / name).write_bytes(blob)
        apps.append({k: meta[k] for k in ("id", "name", "version", "summary", "license")}
                    | {"file": name, "sha256": hashlib.sha256(blob).hexdigest(), "size": len(blob)})
        print(f"{name}  {len(blob)} bytes")
    index = json.dumps({"format": 1, "apps": apps}, indent=2).encode() + b"\n"
    (args.out / "index.json").write_bytes(index)
    subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(args.key), "-rawin",
                    "-in", str(args.out / "index.json"), "-out", str(args.out / "index.json.sig")], check=True)
    print(f"index.json signed; {len(apps)} app(s) in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
