"""The CIDINT software store: a catalogue of signed application bundles.

A software source is a directory or web address holding
  index.json       {"format": 1, "apps": [{id, name, version, summary,
                    license, file, sha256, size}, ...]}
  index.json.sig   Ed25519 signature of index.json by a key trusted in
                    /usr/share/cidint/keys/apps/
  <file>           one .cidapp bundle per app (tar.gz with app.json,
                    icon.png and the program files)
The index is accepted only with a valid signature; a bundle only if its
SHA-256 matches the signed index. Installed apps live in
/data/apps/store/<id>/ (owned by root, read-only to users) and run as the
user who starts them, like any other program: the signature says who made
an app, not that it is harmless.
Only root changes anything here (cidint-store, or cidint-authd on behalf
of a desktop user)."""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request

KEYS = "/usr/share/cidint/keys/apps"
SOURCE_DEFAULT = "/etc/cidint/store-source"
SOURCE_LOCAL = "/data/config/cidint/store-source"
ROOT = "/data/apps/store"
CACHE = "/data/apps/store-cache"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,63}$")
MAX_BUNDLE = 200 * 1024 * 1024


class StoreError(Exception):
    pass


def source() -> str:
    for path in (SOURCE_LOCAL, SOURCE_DEFAULT):
        try:
            with open(path, encoding="utf-8") as f:
                s = f.read().strip()
            if s:
                return s
        except OSError:
            pass
    return ""


def set_source(url: str):
    url = url.strip()
    if url and not url.startswith(("https://", "http://", "file://", "/")):
        raise StoreError("a software source is an http(s):// or file:// address or a directory")
    os.makedirs(os.path.dirname(SOURCE_LOCAL), exist_ok=True)
    tmp = SOURCE_LOCAL + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(url + "\n")
    os.replace(tmp, SOURCE_LOCAL)


MEDIA = "/run/cidint-store-media"


def removable_sources() -> list:
    """Software sources on removable media (USB sticks, SD cards): a folder
    holding index.json at the top of a partition, or in cidint-apps/.
    Partitions that are not mounted yet are mounted read-only, without
    device files, set-user-ID programs or execution, under MEDIA."""
    found = []
    mounts = {}
    with open("/proc/mounts", encoding="utf-8") as f:
        for line in f:
            dev, mnt = line.split()[:2]
            mounts.setdefault(os.path.realpath(dev), mnt.replace("\\040", " "))
    for disk in sorted(os.listdir("/sys/block")):
        try:
            with open(f"/sys/block/{disk}/removable", encoding="utf-8") as f:
                removable = f.read().strip() == "1"
        except OSError:
            continue
        usb = "/usb" in os.path.realpath(f"/sys/block/{disk}")
        if not (removable or usb):
            continue
        parts = [p for p in os.listdir(f"/sys/block/{disk}") if p.startswith(disk)] or [disk]
        for part in sorted(parts):
            dev = f"/dev/{part}"
            mnt = mounts.get(dev)
            if not mnt:
                mnt = os.path.join(MEDIA, part)
                os.makedirs(mnt, exist_ok=True)
                r = subprocess.run(["mount", "-o", "ro,nosuid,nodev,noexec", dev, mnt], capture_output=True)
                if r.returncode != 0:
                    continue
            for cand in (mnt, os.path.join(mnt, "cidint-apps")):
                if os.path.isfile(os.path.join(cand, "index.json")):
                    found.append(cand)
    return found


def allowed_user_source(url: str, home: str) -> bool:
    """Sources a desktop user may choose: web addresses, removable media,
    and folders in the user's own home. Catalogues are signature-checked
    whatever the source, so this only limits what root reads on request."""
    if url.startswith(("https://", "http://")):
        return True
    path = os.path.realpath(url[7:] if url.startswith("file://") else url)
    roots = [MEDIA, "/media", "/run/media", "/mnt", home]
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots if r)


def _fetch(name: str, limit: int) -> bytes:
    src = source()
    if not src:
        raise StoreError("no software source is set (cidint-store source ADDRESS)")
    if src.startswith("/"):
        src = "file://" + src
    url = src.rstrip("/") + "/" + urllib.parse.quote(name)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            data = r.read(limit + 1)
    except Exception as e:  # network, missing file, bad address
        raise StoreError(f"cannot read {url}: {e}") from e
    if len(data) > limit:
        raise StoreError(f"{name} is larger than allowed")
    return data


def _verify(data: bytes, sig: bytes) -> str:
    keys = sorted(k for k in os.listdir(KEYS) if k.endswith(".pem")) if os.path.isdir(KEYS) else []
    if not keys:
        raise StoreError(f"no trusted store key is installed in {KEYS}")
    with tempfile.TemporaryDirectory() as tmp:
        d, s = os.path.join(tmp, "data"), os.path.join(tmp, "sig")
        with open(d, "wb") as f:
            f.write(data)
        with open(s, "wb") as f:
            f.write(sig)
        for k in keys:
            r = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", os.path.join(KEYS, k),
                                "-rawin", "-in", d, "-sigfile", s], capture_output=True)
            if r.returncode == 0:
                return k
    raise StoreError("the catalogue's signature does not match any trusted key")


def refresh() -> dict:
    """Download and verify the catalogue; keep it for list()."""
    data = _fetch("index.json", 4 * 1024 * 1024)
    sig = _fetch("index.json.sig", 4096)
    _verify(data, sig)
    try:
        index = json.loads(data.decode("utf-8"))
    except ValueError as e:
        raise StoreError("the catalogue is not valid JSON") from e
    if index.get("format") != 1 or not isinstance(index.get("apps"), list):
        raise StoreError("unknown catalogue format")
    for a in index["apps"]:
        if not ID_RE.match(str(a.get("id", ""))) or not re.match(r"^[0-9a-f]{64}$", str(a.get("sha256", ""))):
            raise StoreError(f"invalid catalogue entry: {a.get('id')!r}")
    os.makedirs(CACHE, exist_ok=True)
    for name, blob in (("index.json", data), ("index.json.sig", sig)):
        with open(os.path.join(CACHE, name + ".new"), "wb") as f:
            f.write(blob)
        os.replace(os.path.join(CACHE, name + ".new"), os.path.join(CACHE, name))
    return index


def _cached_index() -> dict:
    try:
        with open(os.path.join(CACHE, "index.json"), "rb") as f:
            data = f.read()
        with open(os.path.join(CACHE, "index.json.sig"), "rb") as f:
            sig = f.read()
    except OSError:
        return {"apps": []}
    _verify(data, sig)  # the cache is checked again, not trusted blindly
    return json.loads(data.decode("utf-8"))


def installed() -> dict:
    out = {}
    if os.path.isdir(ROOT):
        for i in sorted(os.listdir(ROOT)):
            try:
                with open(os.path.join(ROOT, i, "app.json"), encoding="utf-8") as f:
                    out[i] = json.load(f)
            except (OSError, ValueError):
                continue
    return out


def catalogue() -> list:
    inst = installed()
    apps = []
    for a in _cached_index().get("apps", []):
        a = dict(a)
        a["installed"] = inst.get(a["id"], {}).get("version", "")
        apps.append(a)
    known = {a["id"] for a in apps}
    for i, meta in inst.items():
        if i not in known:
            apps.append(dict(meta, installed=meta.get("version", ""), file="", sha256=""))
    return sorted(apps, key=lambda a: a.get("name", a["id"]).lower())


def _safe_extract(blob: bytes, dest: str):
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for m in tar.getmembers():
            name = os.path.normpath(m.name)
            if name.startswith(("/", "..")) or "/../" in name or not (m.isfile() or m.isdir()):
                raise StoreError(f"the bundle contains a forbidden entry: {m.name}")
            if m.mode & 0o6000:
                raise StoreError(f"the bundle contains a set-user-ID file: {m.name}")
        for m in tar.getmembers():
            target = os.path.join(dest, os.path.normpath(m.name))
            if m.isdir():
                os.makedirs(target, mode=0o755, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), mode=0o755, exist_ok=True)
            with tar.extractfile(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            os.chmod(target, 0o755 if m.mode & 0o111 else 0o644)


def install(app_id: str) -> dict:
    entry = next((a for a in _cached_index().get("apps", []) if a["id"] == app_id), None)
    if entry is None:
        raise StoreError(f"'{app_id}' is not in the catalogue (refresh first)")
    blob = _fetch(entry["file"], MAX_BUNDLE)
    if hashlib.sha256(blob).hexdigest() != entry["sha256"]:
        raise StoreError("the downloaded bundle does not match the signed catalogue; nothing was installed")
    os.makedirs(ROOT, mode=0o755, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=f".{app_id}.", dir=ROOT)
    try:
        os.chmod(stage, 0o755)
        _safe_extract(blob, stage)
        with open(os.path.join(stage, "app.json"), encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("id") != app_id or meta.get("version") != entry.get("version"):
            raise StoreError("the bundle's app.json does not match the catalogue")
        exe = os.path.normpath(meta.get("exec", ""))
        if not exe or exe.startswith(("/", "..")) or not os.path.isfile(os.path.join(stage, exe)):
            raise StoreError("the bundle names no program to start")
        final = os.path.join(ROOT, app_id)
        with open(os.path.join(stage, "app.ini"), "w", encoding="utf-8") as f:
            f.write(f"name={meta.get('name', app_id)}\nicon={final}/icon.png\npath={final}/{exe}\n")
        old = final + ".old"
        if os.path.exists(final):
            os.rename(final, old)
        os.rename(stage, final)
        shutil.rmtree(old, ignore_errors=True)
        return meta
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def remove(app_id: str):
    if not ID_RE.match(app_id):
        raise StoreError("invalid app id")
    path = os.path.join(ROOT, app_id)
    if not os.path.isdir(path):
        raise StoreError(f"'{app_id}' is not installed")
    shutil.rmtree(path)


def launch_path(app_id: str) -> str:
    try:
        with open(os.path.join(ROOT, app_id, "app.ini"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("path="):
                    return line[5:].strip()
    except OSError:
        pass
    return ""
