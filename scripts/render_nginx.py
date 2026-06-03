#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


TEMPLATE = """server {{
    listen {public_port};
    server_name _;

    location / {{
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
}}
"""

WEBSOCKET_HEADERS = """        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render an Nginx dedicated-port reverse proxy config")
    parser.add_argument("--project", required=True, help="Project name, used for suggested config filename only")
    parser.add_argument("--public-port", required=True, type=int)
    parser.add_argument("--backend-bind", default="127.0.0.1")
    parser.add_argument("--backend-port", required=True, type=int)
    parser.add_argument("--timeout", default=300, type=int)
    parser.add_argument("--websocket", action="store_true")
    parser.add_argument("--out", default="-", help="Output path, or '-' for stdout")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    text = TEMPLATE.format(
        public_port=args.public_port,
        backend_bind=args.backend_bind,
        backend_port=args.backend_port,
        timeout=args.timeout,
        websocket_headers=WEBSOCKET_HEADERS if args.websocket else "",
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
