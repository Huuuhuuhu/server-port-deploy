#!/usr/bin/env python3
"""维护中文部署登记表；兼容旧版英文表头，保留表外内容。"""
from __future__ import annotations
import argparse
import csv
import html
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from safe_io import atomic_write, file_lock, reject_symlink

HEADERS = [
    "Project", "Server", "User URL", "User Port", "Nginx Listen Port",
    "Backend Bind", "Backend Port", "Process Manager", "Unit/Process",
    "App Dir", "Health Check", "Nginx Config", "Security", "Updated", "Notes",
    "Credential Refs",
]
ZH_HEADERS = [
    "项目", "服务器", "访问地址", "用户端口", "Nginx 监听端口", "后端绑定地址",
    "后端端口", "进程管理器", "服务单元/进程", "应用目录", "健康检查",
    "Nginx 配置", "安全措施", "更新日期", "备注", "凭据引用",
]
LEGACY_HEADERS = [h for h in HEADERS if h not in ("Security", "Credential Refs")]
NUMERIC_HEADERS = {"User Port", "Nginx Listen Port", "Backend Port"}
HEADER_MAP = dict(zip(ZH_HEADERS, HEADERS))


@dataclass
class Registry:
    prefix: str
    rows: list[dict[str, str]]
    suffix: str = ""
    legacy: bool = False


def default_registry_path() -> Path:
    return Path.home() / "server-deployments.md"


def template_registry_text() -> str:
    return (Path(__file__).resolve().parent.parent /
            "references/server-deployments-template.md").read_text(encoding="utf-8")


def split_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    cells, current = [], []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text) and text[i + 1] in "\\|":
            current.append(text[i + 1])
            i += 2
            continue
        if char == "|":
            cells.append(html.unescape("".join(current).strip()))
            current = []
        else:
            current.append(char)
        i += 1
    cells.append(html.unescape("".join(current).strip()))
    return cells


def format_cell(value: str) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return (html.escape(text, quote=False).replace("\\", "&#92;")
            .replace("|", "&#124;") or "-")


def format_row(values: list[str]) -> str:
    return "| " + " | ".join(map(format_cell, values)) + " |"


def separator_row() -> str:
    return "|" + "|".join("---:" if h in NUMERIC_HEADERS else "---"
                          for h in HEADERS) + "|"


def load_registry(path: Path) -> Registry:
    reject_symlink(path)
    return load_registry_from_text(
        path.read_text(encoding="utf-8") if path.exists() else template_registry_text())


def load_registry_from_text(text: str) -> Registry:
    return load_registry_from_lines(text.splitlines(keepends=True), text)


def load_registry_from_lines(lines: list[str], raw_text: str) -> Registry:
    # Slice the original text to retain notes and unrelated tables exactly.
    lines = raw_text.splitlines(keepends=True)
    candidates = []
    for i, line in enumerate(lines):
        cells = split_row(line) if line.lstrip().startswith("|") else []
        mapped = [HEADER_MAP.get(cell, cell) for cell in cells]
        if mapped in (HEADERS, HEADERS[:-1], LEGACY_HEADERS):
            candidates.append((i, cells, mapped))
    if len(candidates) != 1:
        raise ValueError("需要唯一、可识别的部署表，未改写文件；请检查表头")
    start, headers, mapped = candidates[0]
    if start + 1 >= len(lines):
        raise ValueError("部署表缺少分隔行")
    separators = split_row(lines[start + 1])
    if len(separators) != len(headers) or not all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in separators):
        raise ValueError("部署表分隔行无效")
    rows = []
    end = start + 2
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        cells = split_row(lines[end])
        if len(cells) != len(headers):
            raise ValueError("部署表列数不匹配，未改写文件；请从备份核对原数据")
        row = {h: "" for h in HEADERS}
        row.update({h: "" if cell == "-" else cell for h, cell in zip(mapped, cells)})
        if row["Project"]:
            rows.append(row)
        end += 1
    keys = [(r["Project"].casefold(), r["Server"].casefold()) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("存在重复项目/服务器记录，未改写文件")
    return Registry("".join(lines[:start]), rows, "".join(lines[end:]),
                    headers != ZH_HEADERS)


def backup_file(path: Path) -> None:
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        atomic_write(path.with_name(path.name + ".bak." + stamp), path.read_bytes())


def write_registry(path: Path, registry: Registry) -> None:
    if registry.legacy:
        backup_file(path)
    table = [format_row(ZH_HEADERS), separator_row()]
    table.extend(format_row([row.get(h, "") for h in HEADERS]) for row in registry.rows)
    prefix = registry.prefix
    for old, new in {
        "# Server Deployments Registry": "# 服务器部署登记表",
        "## Port Ranges": "## 端口约定", "## Deployments": "## 部署记录",
    }.items():
        prefix = prefix.replace(old, new)
    atomic_write(path, (prefix + "\n".join(table) + "\n" + registry.suffix).encode("utf-8"))


def used_ports(rows: list[dict[str, str]], server: str | None = None) -> set[int]:
    return {int(row[h]) for row in rows
            if server is None or row.get("Server", "").casefold() == server.casefold()
            for h in NUMERIC_HEADERS if row.get(h, "").isdigit()}


def port(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("端口必须在 1–65535 内")
    return number


def cmd_list(args: argparse.Namespace) -> None:
    rows = load_registry(args.registry).rows
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(ZH_HEADERS)
        writer.writerows([[r.get(h, "") for h in HEADERS] for r in rows])


def cmd_get(args: argparse.Namespace) -> None:
    rows = [r for r in load_registry(args.registry).rows
            if r["Project"].casefold() == args.project.casefold()
            and (not args.server or r["Server"].casefold() == args.server.casefold())]
    if not rows:
        raise ValueError("没有匹配的部署记录")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def cmd_find_free(args: argparse.Namespace) -> None:
    if args.start > args.end:
        raise ValueError("起始端口不能大于结束端口")
    used = used_ports(load_registry(args.registry).rows, args.server) | set(args.used or [])
    for candidate in range(args.start, args.end + 1):
        if candidate not in used:
            print(candidate)
            return
    raise ValueError("给定范围内没有登记表空闲端口（仍需检查实际监听）")


def cmd_init(args: argparse.Namespace) -> None:
    if args.registry.exists() and not args.force:
        print("登记表已存在：" + str(args.registry))
        return
    backup_file(args.registry)
    atomic_write(args.registry, template_registry_text().encode("utf-8"))
    print(args.registry)


def cmd_upsert(args: argparse.Namespace) -> None:
    registry = load_registry(args.registry)
    key = (args.project.casefold(), args.server.casefold())
    row = next((r for r in registry.rows
                if (r["Project"].casefold(), r["Server"].casefold()) == key), None)
    if row is None:
        if any(getattr(args, field) is None
               for field in ("user_url", "user_port", "backend_port")):
            raise ValueError("新记录必须提供 --user-url、--user-port 和 --backend-port")
        row = {h: "" for h in HEADERS}
        row.update({"Backend Bind": "127.0.0.1", "Process Manager": "systemd",
                    "Nginx Listen Port": str(args.user_port)})
        registry.rows.append(row)
    options = [
        "project", "server", "user_url", "user_port", "nginx_port", "backend_bind",
        "backend_port", "process_manager", "unit", "app_dir", "health_check",
        "nginx_config", "security", "updated", "notes", "credential_refs",
    ]
    for header, option in zip(HEADERS, options):
        value = getattr(args, option)
        if value is not None:
            row[header] = str(value)
    row["Updated"] = args.updated or date.today().isoformat()
    registry.rows.sort(key=lambda r: (
        r["Server"], int(r["User Port"]) if r["User Port"].isdigit() else 0, r["Project"]))
    write_registry(args.registry, registry)
    print(args.registry)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="维护中文服务器部署登记表")
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--allow-root", action="store_true", help="恢复操作，必须显式指定 --registry")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--json", action="store_true", help="稳定的英文机器字段名")
    listing.set_defaults(func=cmd_list)
    get = sub.add_parser("get")
    get.add_argument("--project", required=True)
    get.add_argument("--server")
    get.set_defaults(func=cmd_get)
    init = sub.add_parser("init")
    init.add_argument("--force", action="store_true", help="先备份再重新初始化")
    init.set_defaults(func=cmd_init)
    find = sub.add_parser("find-free")
    find.add_argument("--start", type=port, required=True)
    find.add_argument("--end", type=port, required=True)
    find.add_argument("--server")
    find.add_argument("--used", type=port, nargs="*")
    find.set_defaults(func=cmd_find_free)
    upsert = sub.add_parser("upsert")
    upsert.add_argument("--project", required=True)
    upsert.add_argument("--server", required=True)
    for option in ("user-url", "backend-bind", "process-manager", "unit", "app-dir",
                   "health-check", "nginx-config", "security", "updated", "notes", "credential-refs"):
        upsert.add_argument("--" + option)
    for option in ("user-port", "nginx-port", "backend-port"):
        upsert.add_argument("--" + option, type=port)
    upsert.set_defaults(func=cmd_upsert)
    args = parser.parse_args(argv)
    if args.allow_root and args.registry is None:
        parser.error("--allow-root 必须同时显式指定 --registry")
    if hasattr(os, "geteuid") and os.geteuid() == 0 and not args.allow_root:
        parser.error("请以部署登录用户运行；恢复操作可使用 --allow-root 和显式 --registry")
    args.registry = (args.registry or default_registry_path()).expanduser().absolute()
    return args


def main() -> None:
    try:
        args = parse_args()
        with file_lock(args.registry.with_name(args.registry.name + ".lock")):
            args.func(args)
    except (ValueError, OSError, TimeoutError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
