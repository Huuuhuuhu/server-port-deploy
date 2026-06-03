#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path


HEADERS = [
    "Project",
    "Server",
    "User URL",
    "User Port",
    "Nginx Listen Port",
    "Backend Bind",
    "Backend Port",
    "Process Manager",
    "Unit/Process",
    "App Dir",
    "Health Check",
    "Nginx Config",
    "Updated",
    "Notes",
]


@dataclass
class Registry:
    prefix: str
    rows: list[dict[str, str]]


def default_registry_path() -> Path:
    return Path.home() / "server-deployments.md"


def template_registry_text() -> str:
    template_path = Path(__file__).resolve().parent.parent / "references" / "server-deployments-template.md"
    if template_path.exists():
        return template_path.read_text(encoding="utf-8")
    return (
        "# Server Deployments Registry\n\n"
        "## Port Ranges\n\n"
        "| Purpose | Preferred Range | Notes |\n"
        "|---|---:|---|\n"
        "| User access / Nginx listen ports | 12001-12999 | Public ports users open as `http://SERVER_IP:PORT`. |\n"
        "| Backend service ports | 18001-18999 | Local-only app ports, normally bound to `127.0.0.1`. |\n\n"
        "## Deployments\n\n"
        "| Project | Server | User URL | User Port | Nginx Listen Port | Backend Bind | Backend Port | Process Manager | Unit/Process | App Dir | Health Check | Nginx Config | Updated | Notes |\n"
        "|---|---|---|---:|---:|---|---:|---|---|---|---|---|---|---|\n"
    )


def split_row(line: str) -> list[str]:
    return [cell.strip().replace(r"\|", "|") for cell in line.strip().strip("|").split("|")]


def format_cell(value: str) -> str:
    value = str(value or "").replace("|", r"\|").replace("\n", " ").strip()
    return value or "-"


def format_row(values: list[str]) -> str:
    return "| " + " | ".join(format_cell(value) for value in values) + " |"


def load_registry(path: Path) -> Registry:
    if not path.exists():
        return load_registry_from_text(template_registry_text())

    lines = path.read_text(encoding="utf-8").splitlines()
    return load_registry_from_lines(lines, path.read_text(encoding="utf-8"))


def load_registry_from_text(text: str) -> Registry:
    return load_registry_from_lines(text.splitlines(), text)


def load_registry_from_lines(lines: list[str], raw_text: str) -> Registry:
    table_start = None
    for index, line in enumerate(lines):
        cells = split_row(line) if line.strip().startswith("|") else []
        if cells == HEADERS:
            table_start = index
            break
    if table_start is None:
        return Registry(prefix=raw_text.rstrip() + "\n\n", rows=[])

    prefix = "\n".join(lines[:table_start]).rstrip() + "\n\n"
    rows: list[dict[str, str]] = []
    for line in lines[table_start + 2 :]:
        if not line.strip().startswith("|"):
            continue
        cells = split_row(line)
        if len(cells) < len(HEADERS):
            cells += [""] * (len(HEADERS) - len(cells))
        row = dict(zip(HEADERS, cells[: len(HEADERS)]))
        if row.get("Project") and row["Project"] != "-":
            rows.append(row)
    return Registry(prefix=prefix, rows=rows)


def write_registry(path: Path, registry: Registry) -> None:
    lines = [registry.prefix.rstrip(), "", format_row(HEADERS), "|---|---|---|---:|---:|---|---:|---|---|---|---|---|---|---|"]
    for row in registry.rows:
        lines.append(format_row([row.get(header, "") for header in HEADERS]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def used_ports(rows: list[dict[str, str]]) -> set[int]:
    ports: set[int] = set()
    for row in rows:
        for key in ("User Port", "Nginx Listen Port", "Backend Port"):
            value = row.get(key, "")
            if re.fullmatch(r"\d+", value):
                ports.add(int(value))
    return ports


def cmd_list(args: argparse.Namespace) -> None:
    registry = load_registry(args.registry)
    writer = csv.DictWriter(sys.stdout, fieldnames=HEADERS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(registry.rows)


def cmd_get(args: argparse.Namespace) -> None:
    registry = load_registry(args.registry)
    project = args.project.lower()
    server = args.server.lower() if args.server else None
    rows = [
        row
        for row in registry.rows
        if row.get("Project", "").lower() == project
        and (server is None or row.get("Server", "").lower() == server)
    ]
    if not rows:
        raise SystemExit("No matching deployment found")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def cmd_find_free(args: argparse.Namespace) -> None:
    registry = load_registry(args.registry)
    used = used_ports(registry.rows)
    used.update(args.used or [])
    for port in range(args.start, args.end + 1):
        if port not in used:
            print(port)
            return
    raise SystemExit(f"No free port in {args.start}-{args.end}")


def cmd_init(args: argparse.Namespace) -> None:
    if args.registry.exists() and not args.force:
        print(f"Registry already exists: {args.registry}")
        return
    args.registry.parent.mkdir(parents=True, exist_ok=True)
    args.registry.write_text(template_registry_text(), encoding="utf-8")
    print(str(args.registry))


def cmd_upsert(args: argparse.Namespace) -> None:
    registry = load_registry(args.registry)
    key = (args.project.lower(), args.server.lower())
    row = next((item for item in registry.rows if (item.get("Project", "").lower(), item.get("Server", "").lower()) == key), None)
    if row is None:
        row = {header: "" for header in HEADERS}
        registry.rows.append(row)

    updates = {
        "Project": args.project,
        "Server": args.server,
        "User URL": args.user_url,
        "User Port": str(args.user_port),
        "Nginx Listen Port": str(args.nginx_port or args.user_port),
        "Backend Bind": args.backend_bind,
        "Backend Port": str(args.backend_port),
        "Process Manager": args.process_manager,
        "Unit/Process": args.unit,
        "App Dir": args.app_dir,
        "Health Check": args.health_check,
        "Nginx Config": args.nginx_config,
        "Updated": args.updated or date.today().isoformat(),
        "Notes": args.notes,
    }
    for field, value in updates.items():
        if value not in (None, ""):
            row[field] = value

    registry.rows.sort(key=lambda item: (item.get("Server", ""), int(item.get("User Port", "0") or 0), item.get("Project", "")))
    write_registry(args.registry, registry)
    print(str(args.registry))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintain the server deployments Markdown registry")
    parser.add_argument("--registry", type=Path, default=default_registry_path())
    parser.add_argument("--allow-root", action="store_true", help="Allow root execution for explicit recovery operations")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    get = sub.add_parser("get")
    get.add_argument("--project", required=True)
    get.add_argument("--server", default="")
    get.set_defaults(func=cmd_get)

    init = sub.add_parser("init")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    find = sub.add_parser("find-free")
    find.add_argument("--start", type=int, required=True)
    find.add_argument("--end", type=int, required=True)
    find.add_argument("--used", type=int, nargs="*")
    find.set_defaults(func=cmd_find_free)

    upsert = sub.add_parser("upsert")
    upsert.add_argument("--project", required=True)
    upsert.add_argument("--server", required=True)
    upsert.add_argument("--user-url", required=True)
    upsert.add_argument("--user-port", type=int, required=True)
    upsert.add_argument("--nginx-port", type=int)
    upsert.add_argument("--backend-bind", default="127.0.0.1")
    upsert.add_argument("--backend-port", type=int, required=True)
    upsert.add_argument("--process-manager", default="systemd")
    upsert.add_argument("--unit", default="")
    upsert.add_argument("--app-dir", default="")
    upsert.add_argument("--health-check", default="")
    upsert.add_argument("--nginx-config", default="")
    upsert.add_argument("--updated", default="")
    upsert.add_argument("--notes", default="")
    upsert.set_defaults(func=cmd_upsert)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if hasattr(os, "geteuid") and os.geteuid() == 0 and not args.allow_root:
        raise SystemExit(
            "Refusing to run registry commands as root. "
            "Run as the deployment login user so the registry stays at ~/server-deployments.md. "
            "Use --allow-root only for explicit recovery."
        )
    args.func(args)


if __name__ == "__main__":
    main()
