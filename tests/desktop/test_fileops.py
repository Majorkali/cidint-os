"""Tests of the CIDINT file manager's file operations (no GUI needed).

    python3 -m unittest discover -s tests/desktop -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "desktop"))
from cidint_desktop import fileops  # noqa: E402
from cidint_desktop.fileops import FileOpError  # noqa: E402


class FileOpsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.docs = os.path.join(self.home, "Documents")
        os.mkdir(self.docs)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text="hello\n"):
        p = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_list_folders_first_hidden_skipped(self):
        self.write("b.txt")
        self.write("A.txt")
        self.write(".secret")
        os.mkdir(os.path.join(self.home, "zeta"))
        names = [e.name for e in fileops.list_dir(self.home)]
        self.assertEqual(names, ["Documents", "zeta", "A.txt", "b.txt"])
        self.assertIn(".secret", [e.name for e in fileops.list_dir(self.home, show_hidden=True)])

    def test_new_folder_and_bad_names(self):
        p = fileops.new_folder(self.home, "Projects")
        self.assertTrue(os.path.isdir(p))
        for bad in ("", ".", "..", "a/b"):
            with self.assertRaises(FileOpError):
                fileops.new_folder(self.home, bad)
        with self.assertRaises(FileOpError):
            fileops.new_folder(self.home, "Projects")

    def test_rename_refuses_to_overwrite(self):
        a, b = self.write("a.txt", "A"), self.write("b.txt", "B")
        with self.assertRaises(FileOpError):
            fileops.rename(a, "b.txt")
        self.assertEqual(open(b, encoding="utf-8").read(), "B")
        new = fileops.rename(a, "c.txt")
        self.assertEqual(open(new, encoding="utf-8").read(), "A")
        self.assertFalse(os.path.exists(a))

    def test_copy_keeps_both_with_free_names(self):
        a = self.write("note.txt", "N")
        out1 = fileops.copy_into([a], self.home)
        out2 = fileops.copy_into([a], self.home)
        self.assertEqual([os.path.basename(p) for p in out1 + out2], ["note (copy).txt", "note (copy 2).txt"])
        self.assertEqual(open(a, encoding="utf-8").read(), "N")

    def test_copy_folder_recursively_and_not_into_itself(self):
        self.write("Documents/sub/x.txt", "X")
        dest = fileops.new_folder(self.home, "Backup")
        out = fileops.copy_into([self.docs], dest)
        self.assertEqual(open(os.path.join(out[0], "sub", "x.txt"), encoding="utf-8").read(), "X")
        with self.assertRaises(FileOpError):
            fileops.copy_into([self.docs], os.path.join(self.docs, "sub"))

    def test_move(self):
        a = self.write("m.txt", "M")
        out = fileops.move_into([a], self.docs)
        self.assertFalse(os.path.exists(a))
        self.assertEqual(open(out[0], encoding="utf-8").read(), "M")
        self.assertEqual(fileops.move_into(out, self.docs), out)  # already there: nothing moves
        with self.assertRaises(FileOpError):
            fileops.move_into([self.docs], os.path.join(self.docs, "inner"))

    def test_trash_restore_and_empty(self):
        a = self.write("Documents/report.txt", "R")
        b = self.write("Documents/report2.txt", "R2")
        fileops.trash([a, b], self.home)
        self.assertFalse(os.path.exists(a))
        entries = dict(fileops.trash_entries(self.home))
        self.assertEqual(os.path.normpath(entries["report.txt"]), os.path.normpath(a))
        # The same name again does not overwrite what is already in the trash.
        a2 = self.write("Documents/report.txt", "R-new")
        fileops.trash([a2], self.home)
        self.assertEqual(len(fileops.trash_entries(self.home)), 3)
        restored = fileops.restore("report.txt", self.home)
        self.assertEqual(open(restored, encoding="utf-8").read(), "R")
        self.assertEqual(fileops.empty_trash(self.home), 2)
        self.assertEqual(fileops.trash_entries(self.home), [])
        self.assertEqual(open(a, encoding="utf-8").read(), "R")

    def test_trash_cannot_trash_itself(self):
        self.write("x.txt")
        fileops.trash([os.path.join(self.home, "x.txt")], self.home)
        with self.assertRaises(FileOpError):
            fileops.trash([fileops.trash_dir(self.home)], self.home)

    def test_is_text(self):
        t = self.write("t.txt", "plain text ü\n")
        bpath = os.path.join(self.home, "b.bin")
        with open(bpath, "wb") as f:
            f.write(b"\x00\x01\x02binary")
        self.assertTrue(fileops.is_text(t))
        self.assertFalse(fileops.is_text(bpath))

    def test_human_size(self):
        self.assertEqual(fileops.human_size(512), "512 bytes")
        self.assertEqual(fileops.human_size(2048), "2.0 KB")
        self.assertEqual(fileops.human_size(5 * 1024 ** 3), "5.0 GB")


if __name__ == "__main__":
    unittest.main()
