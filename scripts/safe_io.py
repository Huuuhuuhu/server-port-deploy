"""Small atomic-file and advisory-lock helpers (Python 3.9+, no dependencies)."""
from __future__ import annotations

import os
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("拒绝通过符号链接读写受管理文件")


def absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def reject_symlink_tree(path: Path) -> None:
    for component in [*reversed(path.parents), path]:
        reject_symlink(component)


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    reject_symlink(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if os.name == "posix":
                os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        reject_symlink(path)
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def private_path(path: Path, directory: bool = False) -> None:
    """Check existing Linux vault paths; don't silently repair unsafe permissions."""
    reject_symlink(path)
    info = path.stat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(info.st_mode):
        raise ValueError("凭据路径类型不正确")
    if os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise ValueError("凭据路径必须归当前用户所有，目录权限 0700、文件权限 0600")


def private_directory(path: Path) -> None:
    """Never follow directory symlinks, including ancestors."""
    path = absolute_path(path)
    reject_symlink_tree(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_path(path, directory=True)


@contextmanager
def file_lock(path: Path, timeout: float = 15):
    """Lock the full read/modify/write transaction; lock files intentionally remain."""
    path.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink(path)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "r+b") as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("文件正被其他部署进程使用，请稍后重试") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
