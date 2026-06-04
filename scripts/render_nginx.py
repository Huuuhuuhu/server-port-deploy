#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


PROXY_BLOCK = """    location / {{
        proxy_pass http://{backend_bind}:{backend_port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
{websocket_headers}
        proxy_connect_timeout 10s;
        proxy_send_timeout {timeout}s;
        proxy_read_timeout {timeout}s;
    }}
"""

HTTP_TEMPLATE = """server {{
    listen {public_port};
    server_name {server_name};

{proxy_block}
}}
"""

HTTPS_TEMPLATE = """server {{
    listen {public_port} ssl http2;
    server_name {server_name};

    ssl_certificate {ssl_certificate};
    ssl_certificate_key {ssl_certificate_key};

{proxy_block}
}}
"""

REDIRECT_TEMPLATE = """server {{
    listen 80;
    server_name {server_name};

    return 301 https://$host$request_uri;
}}

"""

WEBSOCKET_HEADERS = """        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render an Nginx dedicated-port reverse proxy config")
    parser.add_argument("--project", required=True, help="Project name, used for suggested config filename only")
    parser.add_argument("--public-port", type=int, help="Defaults to 80 for HTTP and 443 for HTTPS")
    parser.add_argument("--server-name", default="_")
    parser.add_argument("--backend-bind", default="127.0.0.1")
    parser.add_argument("--backend-port", required=True, type=int)
    parser.add_argument("--timeout", default=300, type=int)
    parser.add_argument("--websocket", action="store_true")
    parser.add_argument("--https", action="store_true", help="Render an SSL/TLS server block")
    parser.add_argument("--ssl-certificate", default="", help="Path to fullchain.pem or equivalent certificate")
    parser.add_argument("--ssl-certificate-key", default="", help="Path to privkey.pem or equivalent key")
    parser.add_argument("--redirect-http", action="store_true", help="Add a port 80 redirect to HTTPS")
    parser.add_argument("--out", default="-", help="Output path, or '-' for stdout")
    args = parser.parse_args()

    if args.public_port is None:
        args.public_port = 443 if args.https else 80

    if args.https:
        if not args.ssl_certificate and args.server_name != "_":
            args.ssl_certificate = f"/etc/letsencrypt/live/{args.server_name}/fullchain.pem"
        if not args.ssl_certificate_key and args.server_name != "_":
            args.ssl_certificate_key = f"/etc/letsencrypt/live/{args.server_name}/privkey.pem"
        if not args.ssl_certificate or not args.ssl_certificate_key:
            parser.error("--https requires --ssl-certificate and --ssl-certificate-key when --server-name is '_'")

    return args


def main() -> None:
    args = parse_args()
    proxy_block = PROXY_BLOCK.format(
        backend_bind=args.backend_bind,
        backend_port=args.backend_port,
        timeout=args.timeout,
        websocket_headers=WEBSOCKET_HEADERS if args.websocket else "",
    ).rstrip()
    if args.https:
        text = HTTPS_TEMPLATE.format(
            public_port=args.public_port,
            server_name=args.server_name,
            ssl_certificate=args.ssl_certificate,
            ssl_certificate_key=args.ssl_certificate_key,
            proxy_block=proxy_block,
        )
        if args.redirect_http:
            text = REDIRECT_TEMPLATE.format(server_name=args.server_name) + text
    else:
        text = HTTP_TEMPLATE.format(
            public_port=args.public_port,
            server_name=args.server_name,
            proxy_block=proxy_block,
        )
    if args.out == "-":
        print(text, end="")
        return
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(str(path))


if __name__ == "__main__":
    main()
