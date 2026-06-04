# Server Deployments Registry

This file is the source of truth for known project deployments managed through this skill. Keep the live copy at `~/server-deployments.md` on the target Linux server.

## Port Ranges

| Purpose | Preferred Range | Notes |
|---|---:|---|
| HTTP user access / Nginx listen port | 80 | Default public HTTP entrypoint when free on this server/IP. |
| HTTPS user access / Nginx listen port | 443 | Preferred when a domain points to the server and a TLS certificate is configured. |
| Additional dedicated public ports | 12001-12999 | Use for multiple independent projects on the same IP without domain/path routing. |
| Backend service ports | 18001-18999 | Local-only app ports, normally bound to `127.0.0.1`. |

## Deployments

| Project | Server | User URL | User Port | Nginx Listen Port | Backend Bind | Backend Port | Process Manager | Unit/Process | App Dir | Health Check | Nginx Config | Security | Updated | Notes |
|---|---|---|---:|---:|---|---:|---|---|---|---|---|---|---|---|

> **Security column**: record auth method (none / IP-allowlist / Basic-Auth / app-login / anti-bot), whether API docs are disabled, whether secret fields are masked, and residual risk. "none (open by user's explicit choice)" is valid; silent omission is not.
