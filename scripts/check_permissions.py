#!/usr/bin/env python3
"""以应用账号只读检查 Linux 文件权限；不修复权限、不读取文件内容。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys


def absolute_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("请使用已核实的绝对路径")
    return Path(os.path.abspath(path))


def inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


class Audit:
    def __init__(self, writable: list[Path]):
        self.uid = os.geteuid()
        self.writable = writable
        self.issues: list[dict[str, str]] = []
        self.seen: set[Path] = set()
        self.ancestor_checks: set[Path] = set()
        self.checked = 0

    def issue(self, path: Path, code: str, message: str) -> None:
        self.issues.append({"path": str(path), "code": code, "message": message})

    def ancestors(self, path: Path) -> None:
        """A read-only file can still be replaced through a writable ancestor."""
        child = path
        for parent in path.parents:
            if child in self.ancestor_checks:
                break
            self.ancestor_checks.add(child)
            info = parent.stat()
            writable = os.access(parent, os.W_OK | os.X_OK, effective_ids=True)
            sticky_protects = (info.st_mode & stat.S_ISVTX
                               and info.st_uid != self.uid
                               and child.lstat().st_uid != self.uid)
            if info.st_uid == self.uid or (writable and not sticky_protects):
                self.issue(parent, "replaceable_ancestor", "运行账号可经上层目录替换受保护路径")
            child = parent

    def readonly(self, path: Path) -> None:
        try:
            target = path.resolve(strict=True)
            # Only explicitly named data/cache directories are excluded.
            if any(inside(target, data) for data in self.writable):
                return
            self.ancestors(path)
            if target != path:
                self.ancestors(target)
            if target in self.seen:
                return
            self.seen.add(target)
            info = target.stat()
            self.checked += 1
            if info.st_uid == self.uid:
                self.issue(target, "owned_by_runtime", "运行账号拥有此路径，可自行放宽权限")
            if os.access(target, os.W_OK, effective_ids=True):
                self.issue(target, "writable", "运行账号具有写权限")
            if stat.S_ISDIR(info.st_mode):
                for child in sorted(target.iterdir()):
                    self.readonly(child)
            elif not stat.S_ISREG(info.st_mode):
                self.issue(target, "unsupported_type", "只读范围中包含非普通文件或目录")
            elif not os.access(target, os.R_OK, effective_ids=True):
                self.issue(target, "not_readable", "运行账号不能读取所需代码或配置")
        except (OSError, RuntimeError):
            self.issue(path, "cannot_inspect", "路径缺失、链接循环或访问受限，无法完成检查")

    def data_directory(self, path: Path) -> None:
        if not path.is_dir() or not os.access(path, os.R_OK | os.W_OK | os.X_OK,
                                               effective_ids=True):
            self.issue(path, "data_not_accessible", "运行账号不能读写或进入指定数据目录")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-user", required=True, help="预期的非 root 运行账号；本工具不切换用户")
    parser.add_argument("--read-only", type=absolute_path, action="append", required=True,
                        help="应只读且可读取的文件或目录；递归检查，可重复")
    parser.add_argument("--writable", type=absolute_path, action="append", default=[],
                        help="确需读写的数据/缓存目录；从只读树中排除，可重复")
    args = parser.parse_args(argv)
    if not sys.platform.startswith("linux"):
        parser.error("权限验收必须在目标 Linux 环境执行，不能以 Windows 检查代替")
    import pwd
    try:
        account = pwd.getpwnam(args.expected_user)
    except KeyError:
        parser.error("预期运行账号不存在")
    if os.geteuid() == 0 or os.getuid() != account.pw_uid or os.geteuid() != account.pw_uid:
        parser.error("请直接以预期的非 root 应用账号运行，例如 runuser -u APP -- python3 ...")
    try:
        readonly = [path.resolve(strict=True) for path in args.read_only]
        writable = [path.resolve(strict=True) for path in args.writable]
    except (OSError, RuntimeError):
        parser.error("检查范围中的路径缺失、链接循环或不可访问")
    if any(inside(code, data) for code in readonly for data in writable):
        parser.error("数据目录不能等于或包含只读根目录，不能排除整棵代码或工具目录")
    audit = Audit(writable)
    for path in args.read_only:
        audit.readonly(path)
    for path in args.writable:
        audit.data_directory(path)
    print(json.dumps({"ok": not audit.issues, "user": account.pw_name,
                      "uid": os.geteuid(), "gid": os.getegid(), "groups": os.getgroups(),
                      "checked": audit.checked, "writable": [str(p) for p in args.writable],
                      "issues": audit.issues}, ensure_ascii=False, indent=2))
    return 1 if audit.issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
