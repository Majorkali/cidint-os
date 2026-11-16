"""File operations of the CIDINT file manager, without any GUI, so they
can be tested on their own (tests/desktop/test_fileops.py).

Deleting moves to the trash (freedesktop.org trash layout in
~/.local/share/Trash); nothing is removed for good except by emptying the
trash, which the GUI asks about first."""
import os
import shutil
import stat
import time
import urllib.parse
from dataclasses import dataclass


class FileOpError(Exception):
    pass


@dataclass
class Entry:
    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float
    is_link: bool


def list_dir(path: str, show_hidden: bool = False) -> list:
    """Folders first, then files, each sorted by name (case-insensitive)."""
    out = []
    try:
        names = os.listdir(path)
    except OSError as e:
        raise FileOpError(f"cannot open {path}: {e.strerror}") from e
    for name in names:
        if not show_hidden and name.startswith("."):
            continue
        p = os.path.join(path, name)
        try:
            st = os.stat(p)
            is_dir = stat.S_ISDIR(st.st_mode)
            size, mtime = st.st_size, st.st_mtime
        except OSError:  # dangling link or vanished
            st = os.lstat(p)
            is_dir, size, mtime = False, 0, st.st_mtime
        out.append(Entry(name, p, is_dir, size, mtime, os.path.islink(p)))
    out.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return out


def human_size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def check_name(name: str) -> str:
    name = name.strip()
    if not name or name in (".", "..") or "/" in name or "\x00" in name:
        raise FileOpError("names cannot be empty, '.', '..' or contain '/'")
    if len(name.encode()) > 255:
        raise FileOpError("the name is too long")
    return name


def new_folder(parent: str, name: str) -> str:
    p = os.path.join(parent, check_name(name))
    if os.path.lexists(p):
        raise FileOpError(f"'{os.path.basename(p)}' already exists")
    try:
        os.mkdir(p)
    except OSError as e:
        raise FileOpError(f"cannot create the folder: {e.strerror}") from e
    return p


def rename(path: str, new_name: str) -> str:
    new = os.path.join(os.path.dirname(path), check_name(new_name))
    if new == path:
        return path
    if os.path.lexists(new):
        raise FileOpError(f"'{os.path.basename(new)}' already exists")
    try:
        os.rename(path, new)
    except OSError as e:
        raise FileOpError(f"cannot rename: {e.strerror}") from e
    return new


def free_name(dest_dir: str, name: str) -> str:
    """name, or 'name (copy)', 'name (copy 2)', ... whichever is free."""
    if not os.path.lexists(os.path.join(dest_dir, name)):
        return name
    stem, ext = os.path.splitext(name)
    if name.startswith(".") and stem == name:
        ext = ""
    n = 1
    while True:
        cand = f"{stem} (copy){ext}" if n == 1 else f"{stem} (copy {n}){ext}"
        if not os.path.lexists(os.path.join(dest_dir, cand)):
            return cand
        n += 1


def _inside(child: str, parent: str) -> bool:
    child, parent = os.path.realpath(child), os.path.realpath(parent)
    return child == parent or child.startswith(parent + os.sep)


def copy_into(paths: list, dest_dir: str) -> list:
    done = []
    for src in paths:
        if os.path.isdir(src) and not os.path.islink(src) and _inside(dest_dir, src):
            raise FileOpError(f"cannot copy '{os.path.basename(src)}' into itself")
        dst = os.path.join(dest_dir, free_name(dest_dir, os.path.basename(src)))
        try:
            if os.path.isdir(src) and not os.path.islink(src):
                shutil.copytree(src, dst, symlinks=True)
            else:
                shutil.copy2(src, dst, follow_symlinks=False)
        except OSError as e:
            raise FileOpError(f"copying '{os.path.basename(src)}' failed: {e.strerror or e}") from e
        done.append(dst)
    return done


def move_into(paths: list, dest_dir: str) -> list:
    done = []
    for src in paths:
        if os.path.realpath(os.path.dirname(src)) == os.path.realpath(dest_dir):
            done.append(src)  # already there
            continue
        if os.path.isdir(src) and not os.path.islink(src) and _inside(dest_dir, src):
            raise FileOpError(f"cannot move '{os.path.basename(src)}' into itself")
        dst = os.path.join(dest_dir, free_name(dest_dir, os.path.basename(src)))
        try:
            shutil.move(src, dst)
        except OSError as e:
            raise FileOpError(f"moving '{os.path.basename(src)}' failed: {e.strerror or e}") from e
        done.append(dst)
    return done


def trash_dir(home: str) -> str:
    return os.path.join(home, ".local", "share", "Trash")


def trash(paths: list, home: str) -> list:
    """Move to the trash with a .trashinfo record (original path, date)."""
    base = trash_dir(home)
    files, info = os.path.join(base, "files"), os.path.join(base, "info")
    os.makedirs(files, mode=0o700, exist_ok=True)
    os.makedirs(info, mode=0o700, exist_ok=True)
    done = []
    for src in paths:
        src = os.path.abspath(src)
        if _inside(src, base) or _inside(base, src):
            raise FileOpError("the trash itself cannot be moved to the trash")
        name = free_name(files, os.path.basename(src))
        while os.path.exists(os.path.join(info, name + ".trashinfo")):
            name = free_name(files, name + "_")
        with open(os.path.join(info, name + ".trashinfo"), "x", encoding="utf-8") as f:
            f.write("[Trash Info]\nPath=%s\nDeletionDate=%s\n"
                    % (urllib.parse.quote(src), time.strftime("%Y-%m-%dT%H:%M:%S")))
        try:
            shutil.move(src, os.path.join(files, name))
        except OSError as e:
            os.unlink(os.path.join(info, name + ".trashinfo"))
            raise FileOpError(f"moving '{os.path.basename(src)}' to the trash failed: {e.strerror or e}") from e
        done.append(os.path.join(files, name))
    return done


def trash_entries(home: str) -> list:
    """(name in trash, original path) pairs."""
    base = trash_dir(home)
    out = []
    for e in list_dir(os.path.join(base, "files"), show_hidden=True) if os.path.isdir(os.path.join(base, "files")) else []:
        orig = ""
        try:
            for line in open(os.path.join(base, "info", e.name + ".trashinfo"), encoding="utf-8"):
                if line.startswith("Path="):
                    orig = urllib.parse.unquote(line[5:].strip())
        except OSError:
            pass
        out.append((e.name, orig))
    return out


def restore(name: str, home: str) -> str:
    base = trash_dir(home)
    orig = dict(trash_entries(home)).get(name)
    if not orig:
        raise FileOpError("the original location is not known")
    if os.path.lexists(orig):
        raise FileOpError(f"'{orig}' exists again; move it away first")
    os.makedirs(os.path.dirname(orig), exist_ok=True)
    shutil.move(os.path.join(base, "files", name), orig)
    os.unlink(os.path.join(base, "info", name + ".trashinfo"))
    return orig


def empty_trash(home: str) -> int:
    base = trash_dir(home)
    n = 0
    for sub in ("files", "info"):
        d = os.path.join(base, sub)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.unlink(p)
            n += sub == "files"
    return n


def is_text(path: str, limit: int = 4096) -> bool:
    try:
        with open(path, "rb") as f:
            chunk = f.read(limit)
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError as e:
        return e.start >= len(chunk) - 3  # a character cut at the end


def free_space(path: str) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize
