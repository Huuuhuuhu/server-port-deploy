#!/usr/bin/env python3
"""Render one reviewed Nginx server; never install or reload it automatically."""
from __future__ import annotations
import argparse
import ipaddress
import re
from pathlib import Path
from safe_io import atomic_write


def port(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("端口必须在 1–65535 内")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="生成 Nginx 反向代理配置草稿")
    parser.add_argument("--project", required=True, help="日志中的项目标识")
    parser.add_argument("--public-port", type=port)
    parser.add_argument("--server-name", default="_", help="一个域名、IPv4 地址或 _")
    parser.add_argument("--backend-bind", default="127.0.0.1", help="后端 IP 地址")
    parser.add_argument("--backend-port", required=True, type=port)
    parser.add_argument("--timeout", default=300, type=int)
    parser.add_argument("--websocket", action="store_true")
    parser.add_argument("--streaming", action="store_true", help="SSE 等流式响应：关闭代理缓冲")
    parser.add_argument("--https", action="store_true")
    parser.add_argument("--ssl-certificate", default="")
    parser.add_argument("--ssl-certificate-key", default="")
    parser.add_argument("--redirect-http", action="store_true", help="添加 80 端口 HTTPS 跳转")
    parser.add_argument("--out", default="-")
    args = parser.parse_args(argv)
    args.public_port = args.public_port or (443 if args.https else 80)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", args.project):
        parser.error("项目标识含不支持的字符")
    if args.server_name != "_" and not re.fullmatch(
            r"(?=.{1,253}\Z)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*"
            r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", args.server_name):
        parser.error("--server-name 只接受单个域名或 IPv4 地址；多个域名请分别审查配置")
    try:
        address = ipaddress.ip_address(args.backend_bind)
        args.backend_bind = f"[{address}]" if address.version == 6 else str(address)
    except ValueError:
        parser.error("--backend-bind 必须是有效 IP 地址")
    if not 1 <= args.timeout <= 86400:
        parser.error("--timeout 必须在 1–86400 秒内")
    if args.redirect_http and (not args.https or args.public_port == 80 or args.server_name == "_"):
        parser.error("--redirect-http 需要 HTTPS、明确域名且 HTTPS 端口不能为 80")
    if args.https:
        if args.server_name != "_":
            args.ssl_certificate = args.ssl_certificate or f"/etc/letsencrypt/live/{args.server_name}/fullchain.pem"
            args.ssl_certificate_key = args.ssl_certificate_key or f"/etc/letsencrypt/live/{args.server_name}/privkey.pem"
        for path in (args.ssl_certificate, args.ssl_certificate_key):
            if not re.fullmatch(r"/[a-zA-Z0-9_./-]+", path):
                parser.error("HTTPS 需要安全的绝对证书路径（不含空格或配置指令字符）")
    elif args.ssl_certificate or args.ssl_certificate_key:
        parser.error("证书参数需要 --https")
    return args


def render(args) -> str:
    upgrade = ""
    if args.websocket:
        # Per-project variable avoids duplicate map names across independent configs.
        variable = "$spd_connection_" + args.project.encode("ascii").hex()
        upgrade = (f"map $http_upgrade {variable} {{\n"
                   "    default upgrade;\n    '' close;\n}\n\n")
        connection = (f"        proxy_set_header Upgrade $http_upgrade;\n"
                      f"        proxy_set_header Connection {variable};")
    else:
        connection = '        proxy_set_header Connection "";'
    tls = ""
    if args.https:
        tls = (f"\n    ssl_certificate {args.ssl_certificate};"
               f"\n    ssl_certificate_key {args.ssl_certificate_key};\n")
    buffering = "        proxy_buffering off;\n" if args.streaming or args.websocket else ""
    server = (
        f"# 项目：{args.project}\nserver {{\n"
        f"    listen {args.public_port}{' ssl' if args.https else ''};\n"
        f"    server_name {args.server_name};\n{tls}\n"
        "    location / {\n"
        f"        proxy_pass http://{args.backend_bind}:{args.backend_port};\n"
        "        proxy_http_version 1.1;\n"
        "        proxy_set_header Host $http_host;\n"
        "        proxy_set_header X-Real-IP $remote_addr;\n"
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        "        proxy_set_header X-Forwarded-Proto $scheme;\n"
        "        proxy_set_header X-Forwarded-Host $http_host;\n"
        "        proxy_set_header X-Forwarded-Port $server_port;\n"
        f"{connection}\n{buffering}"
        "        proxy_connect_timeout 10s;\n"
        f"        proxy_send_timeout {args.timeout}s;\n"
        f"        proxy_read_timeout {args.timeout}s;\n"
        "    }\n}\n"
    )
    if args.redirect_http:
        target = args.server_name + (f":{args.public_port}" if args.public_port != 443 else "")
        server = ("server {\n    listen 80;\n"
                  f"    server_name {args.server_name};\n"
                  f"    return 301 https://{target}$request_uri;\n"
                  "}\n\n" + server)
    return upgrade + server


def main() -> None:
    args = parse_args()
    text = render(args)
    if args.out == "-":
        print(text, end="")
    else:
        path = Path(args.out)
        atomic_write(path, text.encode("utf-8"), mode=0o644)
        print(path)


if __name__ == "__main__":
    main()
