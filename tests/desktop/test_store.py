"""Tests of the software store: signed catalogue, checksums, safe install
and removal. Needs the openssl command (Ed25519).

    python3 -m unittest discover -s tests/desktop -v
"""
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "desktop"))
from cidint_desktop import store  # noqa: E402


def openssl(*args):
    subprocess.run(["openssl", *args], check=True, capture_output=True)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.repo, keys = t / "repo", t / "keys"
        keys.mkdir()
        self.key = t / "dev.key"
        openssl("genpkey", "-algorithm", "ed25519", "-out", str(self.key))
        openssl("pkey", "-in", str(self.key), "-pubout", "-out", str(keys / "test.pem"))
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build-app-repo.py"), "--key", str(self.key),
                        "--out", str(self.repo)], check=True, capture_output=True)
        self.saved = {k: getattr(store, k) for k in ("KEYS", "SOURCE_LOCAL", "SOURCE_DEFAULT", "ROOT", "CACHE")}
        store.KEYS, store.SOURCE_LOCAL, store.SOURCE_DEFAULT = str(keys), str(t / "source"), str(t / "none")
        store.ROOT, store.CACHE = str(t / "apps"), str(t / "cache")
        store.set_source(self.repo.as_uri())

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(store, k, v)
        self.tmp.cleanup()

    def test_refresh_install_remove(self):
        self.assertEqual(len(store.refresh()["apps"]), 3)
        meta = store.install("org.cidint.calculator")
        self.assertEqual(meta["version"], "1.0.0")
        inst = Path(store.ROOT) / "org.cidint.calculator"
        self.assertTrue(os.access(inst / "bin" / "calculator", os.X_OK) or os.name == "nt")
        self.assertIn("path=", (inst / "app.ini").read_text())
        self.assertEqual({a["id"]: a["installed"] for a in store.catalogue()}["org.cidint.calculator"], "1.0.0")
        store.remove("org.cidint.calculator")
        self.assertFalse(inst.exists())

    def test_unsigned_or_altered_catalogue_refused(self):
        idx = self.repo / "index.json"
        data = idx.read_bytes()
        idx.write_bytes(data.replace(b"Calculator", b"Calculatr"))
        with self.assertRaises(store.StoreError):
            store.refresh()
        idx.write_bytes(data)
        (self.repo / "index.json.sig").unlink()
        with self.assertRaises(store.StoreError):
            store.refresh()

    def test_untrusted_key_refused(self):
        other = Path(self.tmp.name) / "other.key"
        openssl("genpkey", "-algorithm", "ed25519", "-out", str(other))
        openssl("pkeyutl", "-sign", "-inkey", str(other), "-rawin", "-in", str(self.repo / "index.json"),
                "-out", str(self.repo / "index.json.sig"))
        with self.assertRaises(store.StoreError):
            store.refresh()

    def test_damaged_bundle_refused_and_nothing_installed(self):
        store.refresh()
        b = self.repo / "org.cidint.sysmonitor-1.0.0.cidapp"
        b.write_bytes(b.read_bytes()[:-4] + b"xxxx")
        with self.assertRaises(store.StoreError):
            store.install("org.cidint.sysmonitor")
        self.assertFalse((Path(store.ROOT) / "org.cidint.sysmonitor").exists())

    def test_path_traversal_rejected(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo("../escape")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(store.StoreError):
                store._safe_extract(buf.getvalue(), d)
            self.assertFalse(os.path.exists(os.path.join(os.path.dirname(d), "escape")))


if __name__ == "__main__":
    unittest.main()
