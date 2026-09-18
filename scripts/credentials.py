#!/usr/bin/env python3
"""Linux credential store; project/environment namespaces are not access controls."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from safe_io import (absolute_path, atomic_write, file_lock, private_directory,
                     private_path, reject_symlink_tree)

MAX_VALUE = 64 * 1024
MAX_VAULT = 4 * 1024 * 1024
ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
ENV = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
# Credentials may not alter the launcher's interpreter/loader or executable lookup.
RESERVED = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "IFS", "ENV", "BASH_ENV",
            "SHELLOPTS", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "NODE_OPTIONS"}
KINDS = {"server": "服务端凭据", "browser-public": "浏览器公开标识", "login": "登录凭据"}


def identifier(value: str) -> str:
    if not ID.fullmatch(value):
        raise argparse.ArgumentTypeError("标识须为 1–64 位小写字母、数字、下划线或短横线")
    return value


def run_age(program: str, arguments: list[str], data: bytes = b"") -> bytes:
    executable = shutil.which(program)
    if not executable:
        raise ValueError("需要安装 age 和 age-keygen，并加入 PATH")
    try:
        result = subprocess.run([executable, *arguments], input=data, capture_output=True,
                                timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("age 执行失败或超时，未输出敏感内容") from None
    if result.returncode:
        # Never include subprocess stderr/stdout: identities or plaintext may be present.
        raise ValueError("age 操作失败；请检查密钥、密文和版本，未改写凭据") from None
    return result.stdout


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Vault:
    """The owning administrator can access all scopes; apps need OS-level isolation."""

    def __init__(self, root: Path, identity: Path):
        self.root = absolute_path(root)
        self.identity = absolute_path(identity)
        if self.identity == self.root or self.root in self.identity.parents:
            raise ValueError("解密密钥必须存放在凭据库目录之外，以便分开备份")

    def prepare(self) -> None:
        private_directory(self.root)
        private_directory(self.identity.parent)

    def identity_check(self) -> None:
        if not self.identity.exists():
            raise ValueError("缺少解密密钥；已有密文时必须恢复原密钥，不能重新生成")
        private_path(self.identity)

    def initialize(self) -> None:
        if self.identity.exists():
            self.identity_check()
            self.recipient()
            return
        if any(self.root.iterdir()):  # lock is created before this call
            unexpected = [p for p in self.root.iterdir() if p.name != ".lock"]
            if unexpected:
                raise ValueError("凭据库非空但密钥缺失，请恢复原密钥")
        # age-keygen writes a new identity to stdout; it stays in memory until atomic write.
        identity = run_age("age-keygen", [])
        if b"AGE-SECRET-KEY-" not in identity:
            raise ValueError("age-keygen 没有返回有效密钥")
        atomic_write(self.identity, identity)
        self.identity_check()

    def recipient(self) -> str:
        self.identity_check()
        output = run_age("age-keygen", ["-y", str(self.identity)]).decode("ascii").strip()
        if not re.fullmatch(r"age1[0-9a-z]+", output):
            raise ValueError("仅支持 age 原生 X25519 identity")
        return output

    def scope_path(self, project: str, environment: str) -> Path:
        identifier(project)
        identifier(environment)
        directory = self.root / project
        if directory.exists() or directory.is_symlink():
            private_directory(directory)
        return directory / (environment + ".age")

    def read(self, project: str, environment: str, missing_ok: bool = False) -> dict:
        self.identity_check()
        path = self.scope_path(project, environment)
        if not path.exists():
            if path.is_symlink():
                raise ValueError("拒绝读取符号链接")
            if missing_ok:
                return {"version": 1, "project": project, "environment": environment, "records": {}}
            raise ValueError("指定项目/环境没有凭据，未启动应用")
        private_path(path)
        if path.stat().st_size > MAX_VAULT:
            raise ValueError("凭据库超过大小上限")
        plaintext = run_age("age", ["--decrypt", "-i", str(self.identity)], path.read_bytes())
        try:
            payload = json.loads(plaintext)
            valid = (isinstance(payload, dict) and payload.get("version") == 1
                     and payload.get("project") == project
                     and payload.get("environment") == environment
                     and isinstance(payload.get("records"), dict))
            if not valid:
                raise ValueError()
            for name, record in payload["records"].items():
                identifier(name)
                if (not isinstance(record, dict)
                        or set(record) != {"value", "purpose", "kind", "created", "updated"}
                        or any(not isinstance(v, str) for v in record.values())
                        or record["kind"] not in KINDS
                        or not record["value"] or "\0" in record["value"]
                        or len(record["value"].encode("utf-8")) > MAX_VALUE):
                    raise ValueError()
        except (ValueError, TypeError, argparse.ArgumentTypeError):
            raise ValueError("凭据数据格式或项目/环境不匹配，未输出内容") from None
        return payload

    def save(self, payload: dict) -> None:
        data = json_bytes(payload)
        if len(data) > MAX_VAULT - 4096:
            raise ValueError("凭据库超过大小上限")
        encrypted = run_age("age", ["-r", self.recipient()], data)
        path = self.scope_path(payload["project"], payload["environment"])
        private_directory(path.parent)
        if path.exists():
            private_path(path)
        atomic_write(path, encrypted)

    def put(self, args, raw: bytes) -> None:
        if not raw or len(raw) > MAX_VALUE or b"\0" in raw:
            raise ValueError("凭据不能为空、含 NUL 或超过 64 KiB")
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("凭据必须为 UTF-8 文本") from None
        # Do not strip or normalize: spaces, case, punctuation and newlines are data.
        payload = self.read(args.project, args.environment, missing_ok=True)
        old = payload["records"].get(args.name)
        if old and not args.replace:
            raise ValueError("凭据已存在；轮换时请显式使用 --replace")
        payload["records"][args.name] = {
            "value": value,
            "purpose": args.purpose if args.purpose is not None else (old or {}).get("purpose", ""),
            "kind": args.kind if args.kind is not None else (old or {}).get("kind", "server"),
            "created": old["created"] if old else utc_now(),
            "updated": utc_now(),
        }
        self.save(payload)

    def metadata(self, project: str, environment: str) -> list[dict]:
        records = self.read(project, environment)["records"]
        return [{"reference": f"{project}/{environment}/{name}",
                 **{key: record[key] for key in ("purpose", "kind", "created", "updated")}}
                for name, record in sorted(records.items())]

    def all_metadata(self) -> list[dict]:
        self.identity_check()
        rows = []
        for directory in sorted(self.root.iterdir()):
            if directory.name == ".lock":
                continue
            private_path(directory, directory=True)
            identifier(directory.name)
            for path in sorted(directory.iterdir()):
                if path.suffix != ".age":
                    raise ValueError("凭据库包含未知文件，请人工核对")
                rows.extend(self.metadata(directory.name, identifier(path.stem)))
        return rows

    def bindings(self, args) -> dict[str, str]:
        records = self.read(args.project, args.environment)["records"]
        result = {}
        for binding in args.bind:
            env, sep, name = binding.partition("=")
            if (not sep or not ENV.fullmatch(env) or env in RESERVED
                    or env.startswith(("LD_", "DYLD_", "PYTHON"))):
                raise ValueError("绑定必须为安全的环境变量名=凭据名，不能修改启动器/加载器")
            identifier(name)
            if env in result:
                raise ValueError("环境变量重复绑定")
            if name not in records:
                raise ValueError("绑定的凭据不存在，未启动应用")
            result[env] = records[name]["value"]
        return result


def catalog(rows: list[dict]) -> bytes:
    from registry import format_row
    lines = [
        "# 服务器凭据目录", "",
        "本文件仅记录元数据，不含凭据值；由 credentials.py catalog 生成。用途说明不得填写密码或 Key。",
        "", "| 凭据引用 | 用途 | 类型 | 创建时间 | 更新时间 |",
        "|---|---|---|---|---|",
    ]
    lines.extend(format_row([r["reference"], r["purpose"], KINDS[r["kind"]],
                             r["created"], r["updated"]]) for r in rows)
    return ("\n".join(lines) + "\n").encode("utf-8")


def launch(args, bindings: dict[str, str]) -> None:
    command = args.child
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise ValueError("exec 需要 -- 后的应用启动命令")
    environment = os.environ.copy()
    environment.update(bindings)
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if args.as_user:
        if os.name != "posix" or os.geteuid() != 0:
            raise ValueError("--as-user 需要 Linux root 启动器")
        import pwd
        account = pwd.getpwnam(args.as_user)
        if account.pw_uid == 0:
            raise ValueError("--as-user 必须是非 root 应用账号")
        os.initgroups(account.pw_name, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
        environment.update(HOME=account.pw_dir, USER=account.pw_name, LOGNAME=account.pw_name)
    # os.exec replaces the wrapper: systemd sends signals directly to the application.
    os.execvpe(command[0], command, environment)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="服务器集中加密凭据：不提供明文 get 命令",
        epilog="项目/环境是命名范围，不提供同账号应用间的权限隔离；隔离依赖独立运行账号与操作系统权限。")
    parser.add_argument("--store", type=Path,
                        default=Path.home() / ".local/share/server-port-deploy/credentials")
    parser.add_argument("--identity", type=Path,
                        default=Path.home() / ".config/server-port-deploy/identity.txt")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    for command in ("put", "list", "check", "exec"):
        scope = sub.add_parser(command)
        scope.add_argument("--project", type=identifier, required=True, help="项目命名范围，不是访问权限")
        scope.add_argument("--environment", type=identifier, required=True, help="环境命名范围，不是访问权限")
        if command == "put":
            scope.add_argument("--name", type=identifier, required=True)
            scope.add_argument("--stdin", action="store_true", required=True)
            scope.add_argument("--replace", action="store_true")
            scope.add_argument("--purpose", help="仅填写非敏感用途说明")
            scope.add_argument("--kind", choices=KINDS)
        if command == "check":
            scope.add_argument("--name", type=identifier, action="append", required=True)
        if command == "exec":
            scope.add_argument("--bind", action="append", required=True, metavar="ENV=NAME")
            scope.add_argument("--as-user", help="root 解密后降权；需要隔离的应用须分别使用独立账号")
            scope.add_argument("child", nargs=argparse.REMAINDER)
    listing = sub.add_parser("catalog")
    listing.add_argument("--out", type=Path, default=Path.home() / "server-credentials.md")
    backup = sub.add_parser("backup")
    backup.add_argument("--out", type=Path, required=True, help="必须是新目录；只备份密文，不含 identity")
    return parser.parse_args(argv)


def main() -> None:
    try:
        args = parse_args()
        if os.name == "posix":
            import resource
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        raw = None
        if args.command == "put":
            if sys.stdin.isatty():
                raise ValueError("请通过受保护管道提供 stdin，禁止在命令行参数中填写凭据")
            # Read potentially delayed SSH input before locking out other clients.
            raw = sys.stdin.buffer.read(MAX_VALUE + 1)
        vault = Vault(args.store, args.identity)
        vault.prepare()
        bindings = None
        with file_lock(vault.root / ".lock"):
            if args.command == "init":
                vault.initialize()
                print("凭据库已就绪；请将解密密钥单独备份到可信位置")
            elif args.command == "put":
                vault.put(args, raw)
                print("凭据已加密保存；请更新目录并验证应用")
            elif args.command == "list":
                print(json.dumps(vault.metadata(args.project, args.environment),
                                 ensure_ascii=False, indent=2))
            elif args.command == "check":
                records = vault.read(args.project, args.environment)["records"]
                if not all(name in records for name in args.name):
                    raise ValueError("存在缺失凭据")
                print("指定凭据存在且可解密（不代表服务商仍接受该凭据）")
            elif args.command == "catalog":
                destination = absolute_path(args.out)
                reject_symlink_tree(destination)
                if (destination == vault.identity or destination == vault.root
                        or vault.root in destination.parents
                        or vault.identity.parent in destination.parents):
                    raise ValueError("凭据目录文档必须位于密文库和解密密钥目录之外")
                atomic_write(destination, catalog(vault.all_metadata()))
                print("中文凭据目录已更新")
            elif args.command == "backup":
                vault.all_metadata()  # Validate decryptability before copying.
                destination = absolute_path(args.out)
                reject_symlink_tree(destination)
                if (destination.exists() or destination.is_symlink()
                        or destination == vault.root or vault.root in destination.parents
                        or destination == vault.identity.parent
                        or vault.identity.parent in destination.parents):
                    raise ValueError("备份必须使用凭据库之外的新目录")
                private_directory(destination)
                for project in sorted(vault.root.iterdir()):
                    if project.name == ".lock":
                        continue
                    private_directory(destination / project.name)
                    for cipher in project.glob("*.age"):
                        atomic_write(destination / project.name / cipher.name, cipher.read_bytes())
                print("密文备份完成；未包含解密密钥")
            elif args.command == "exec":
                bindings = vault.bindings(args)
        if bindings is not None:
            launch(args, bindings)
    except (ValueError, OSError, KeyError, TimeoutError, argparse.ArgumentTypeError) as error:
        # OS errors can contain arbitrary filenames/command contents; don't echo those.
        message = str(error) if isinstance(error, ValueError) else "凭据操作失败，请检查路径、权限或启动命令"
        raise SystemExit(message) from None


if __name__ == "__main__":
    main()
