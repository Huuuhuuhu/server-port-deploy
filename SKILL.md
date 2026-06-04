---
name: server-port-deploy
description: Deploy or update small web/API projects on a Linux server behind Nginx, defaulting to public HTTP/HTTPS ports 80/443 when appropriate or dedicated public ports for additional projects. Use when the user asks to deploy a new project, publish a local service to a server IP and port, configure Nginx reverse proxy, create or update a systemd service, avoid port conflicts, support concurrent users, or maintain a server deployment registry with user access ports, Nginx listen ports, and backend service ports.
---

# Server Port Deploy

## Purpose

Deploy small projects on a Linux server with this shape:

```text
user -> http://SERVER_IP/ or https://DOMAIN/
     -> Nginx listen 80, 443, or another PUBLIC_PORT
     -> proxy_pass http://127.0.0.1:BACKEND_PORT
     -> project service
```

Keep the deployment registry at `~/server-deployments.md` on the target server current after every deployment or port change. The skill directory contains only templates and helper scripts; it is not the registry source of truth.

## Core Rules

- Prefer Nginx dedicated-port mode unless the user explicitly asks for domain/path routing.
- Prefer public port `80` for plain HTTP when it is free. Prefer public port `443` for HTTPS when a domain points to the server and a TLS certificate is configured. Use `12001-12999` for additional independent projects on the same IP when domain/path routing is not being used.
- Do not expose app services directly on `0.0.0.0` unless there is a clear reason. Bind app services to `127.0.0.1:<backend_port>` and expose only Nginx's public port.
- Treat three ports separately:
  - **User access port**: the port users type in the browser, such as `http://server-ip/` on port 80, `https://example.com/` on port 443, or `http://server-ip:12001` for an extra dedicated port.
  - **Nginx listen port**: the external port Nginx listens on. In dedicated-port mode this is normally the same as the user access port.
  - **Backend service port**: the local port the app process listens on, such as `127.0.0.1:18001`.
- Before choosing ports, inspect `~/server-deployments.md`, live listeners, Nginx configs, and systemd units. Never rely only on the registry.
- Run registry commands as the normal deployment/login user. The helper script refuses root execution by default so agents do not accidentally create `/root/server-deployments.md`; use `--allow-root` only for an explicit recovery operation with an explicit `--registry` path.
- If docs are incomplete, infer conservatively from `README`, package files, Dockerfile, scripts, logs, and common framework defaults. State assumptions in the final answer.
- Use non-interactive commands. Avoid destructive changes. Back up existing Nginx configs before overwriting.
- After deployment, verify both the backend local URL and the Nginx public URL.
- For updates, preserve the existing public port, backend port, systemd unit, Nginx config path, and registry row unless the user explicitly asks to change them. Stop the old service before replacing runtime files, verify the new version, then remove stale code/build/temp artifacts so the server only keeps the active version and declared persistent data.

## Security Baseline (MANDATORY)

Every deployment MUST go through a security assessment before exposing any port. Security is not optional and not an afterthought — treat it as part of "is this deployment done". The goal is to prevent the classic failures: leaking secrets through an open endpoint, exposing interactive API docs to scanners, or putting an unauthenticated service on the public internet.

### Before deploying — assess
For each service ask and answer explicitly:
1. **Authentication**: Does this service have any access control? If it is fully open and exposes data, mutations, paid resources (LLM keys, quotas), or privileged actions, it MUST get an access layer before going public. Options, strongest first: cloud security-group / firewall IP allowlist → reverse-proxy auth (Nginx Basic Auth) → app-level login → anti-bot challenge (only stops scanners, not humans). Pick based on the user's real threat model and network constraints; ask if unclear.
2. **API docs exposure**: Any framework that auto-serves interactive docs (FastAPI `/docs` `/redoc` `/openapi.json`, GraphQL introspection, Swagger, actuator endpoints) MUST have them disabled in production unless the user explicitly wants them public. For FastAPI: `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`.
3. **Sensitive fields in responses**: Check whether any endpoint returns secrets (API keys, tokens, password hashes, connection strings). Endpoints that echo config MUST mask secret fields (e.g. `sk-1234********cdef`) and accept a sentinel on write so the client never needs the plaintext. A field stored encrypted/hashed in the DB but returned in plaintext over HTTP is a leak.
4. **Bind address**: App process binds `127.0.0.1:<backend_port>`, never `0.0.0.0`, unless there is a clear documented reason. Only Nginx listens publicly.
5. **Secrets at rest**: `.env`, key files, password hashes, encrypted-config blobs are never committed to git and never world-readable. Passwords are hashed (salt + strong hash), never stored plaintext.
6. **Transport**: If the service handles credentials or sensitive data, flag that plain HTTP on a public port sends everything in clear text, and recommend TLS / restricting to trusted networks.

### While deploying — enforce
- Disable framework API docs by default.
- Add the chosen access control before the first public request is possible, not after.
- Mask secret fields in any config-returning endpoint.
- Keep app bound to localhost; expose only via Nginx.
- Never echo secret values back in command output or logs while deploying.

### After deploying — disclose and record
- **Tell the user**, in the final response, exactly which security measures were applied (auth method, docs disabled, fields masked, bind address) and which residual risks remain (e.g. "anti-bot only stops scanners, a human who reads the page can still get in", "service is plain HTTP", "secret existed briefly in a pushed commit — rotate if worried").
- **Record the security posture** in `~/server-deployments.md` for each project: whether it has auth and what kind, whether docs are disabled, whether responses are masked, and any known residual risk. Use the registry's security column / Notes.
- If a service is intentionally left open (user's explicit choice), state that explicitly in both the response and the registry so it is a recorded decision, not an oversight.

## New Deployment Workflow

1. **Orient on the project**
   - Read deployment docs first: `README*`, `docs/*deploy*`, `DEPLOY*`, `package.json`, `pyproject.toml`, `requirements.txt`, `Dockerfile`, service scripts.
   - Identify runtime, install command, build command, start command, health endpoint, required env vars, and persistent paths.
   - If the project has no docs, infer a safe deployment and call out the inference.

2. **Inspect server state**
   - Read `~/server-deployments.md` on the target server.
   - If it does not exist, initialize it from `references/server-deployments-template.md` or by running `scripts/registry.py init`.
   - On the target server, inspect:
     ```bash
     ss -ltnp
     sudo nginx -T
     systemctl list-units --type=service --all
     ls -la /etc/nginx/conf.d /etc/nginx/sites-enabled 2>/dev/null
     ```
   - Compare live state with registry. If they disagree, trust live state for conflict avoidance and update the registry after verifying.

3. **Choose ports**
   - Prefer these public/user/Nginx ports:
     - HTTP: `80` when free on this server/IP.
     - HTTPS: `443` when the user has a domain pointing to the server and TLS can be configured.
     - Additional dedicated ports: `12001-12999` when multiple independent projects share the same IP without domain/path routing.
   - Prefer backend ports in `18001-18999`.
   - If the user specifies a public port, use it only if free in both Nginx and live listeners.
   - Use `scripts/registry.py find-free` for registry suggestions, then confirm with live `ss` and `nginx -T`.

4. **Deploy the app service**
   - Install dependencies in the project’s normal way.
   - Configure the app to bind `127.0.0.1:<backend_port>`.
   - Prefer systemd for long-running services. Use clear unit names, for example `<project>.service`.
   - Start and verify the backend:
     ```bash
     sudo systemctl daemon-reload
     sudo systemctl enable --now <unit>
     systemctl status <unit> --no-pager
     curl -i http://127.0.0.1:<backend_port>/
     ```

5. **Configure Nginx**
   - Generate a dedicated-port server block. See `references/nginx-port-mode.md`.
   - Use `scripts/render_nginx.py` to draft the config when helpful.
   - Install to `/etc/nginx/conf.d/<project>.conf` or the server’s existing convention.
   - Validate and reload:
     ```bash
     sudo nginx -t
     sudo systemctl reload nginx
     ```
   - Ensure the cloud security group and host firewall allow the public port.

6. **Verify externally**
   - From the server:
     ```bash
     curl -i http://127.0.0.1:<backend_port>/
     curl -i http://127.0.0.1:<public_port>/
     ```
   - If possible, verify from the user side:
     ```text
     http://SERVER_IP:<public_port>
     ```

7. **Update the registry**
   - Update `~/server-deployments.md` with:
     - project name
     - host/server
     - user access URL and port
     - Nginx listen port
     - backend bind address and port
     - process manager/systemd unit
     - app directory
     - Nginx config path
     - health check
     - **security posture** (auth method or "none/open by choice", API docs disabled?, sensitive fields masked?, residual risk)
     - update date and notes
   - Use `scripts/registry.py upsert` when possible.

## Existing Deployment Update Workflow

Use this when the project is already deployed and the user asks to update, redeploy, replace with a new version, pull latest code, or clean up an old version.

1. **Load the existing deployment**
   - Read `~/server-deployments.md` as the normal deployment user, not with `sudo`.
   - Use `scripts/registry.py get --project "<project>" --server "<server>"` when possible.
   - Confirm the live systemd unit, app directory, Nginx config, public port, and backend port from the server itself:
     ```bash
     systemctl cat <unit>
     sudo nginx -T
     ss -ltnp
     ```
   - If registry and live state disagree, trust live state for safety and update the registry after verification.

2. **Plan what must persist**
   - Preserve `.env`, user uploads, databases, and explicit shared storage.
   - Do not preserve stale dependency folders, old build output, temporary archives, generated caches, or old release directories unless the user asks for rollback retention.
   - Read `references/update-existing-deployment.md` before making server changes.

3. **Stop, update, and rebuild**
   - Stop the current service before replacing files:
     ```bash
     sudo systemctl stop <unit>
     ```
   - Replace the app with the new version using a clean `rsync --delete` pattern with explicit excludes for persistent paths, or use a `releases/<timestamp>` + `current` symlink layout for safer rollback.
   - Reinstall dependencies and rebuild from the new source. Avoid relying on old `.venv`, `node_modules`, `dist`, or cache folders unless the project documentation explicitly requires it.
   - Update the systemd unit only if the runtime command, app directory, env file, or backend port changed.
   - Update Nginx only if the public port, backend port, timeouts, WebSocket/streaming behavior, or config convention changed. Back up before overwriting.

4. **Restart and verify**
   - Run:
     ```bash
     sudo systemctl daemon-reload
     sudo systemctl start <unit>
     systemctl status <unit> --no-pager
     journalctl -u <unit> -n 80 --no-pager
     curl -i http://127.0.0.1:<backend_port>/
     curl -i http://127.0.0.1:<public_port>/
     ```
   - If verification fails, restore the previous working release or backed-up systemd/Nginx files and leave the registry pointing at the live working version.

5. **Clean stale artifacts**
   - After successful verification, remove old release directories, temporary upload/extract folders, stale build outputs, and Nginx backups created during this update unless the user requested rollback retention.
   - Keep only active code plus documented persistent paths.
   - Check for leftovers with targeted commands such as:
     ```bash
     find <app_dir> -maxdepth 2 \( -name '*.bak.*' -o -name '*.tmp' -o -name '__pycache__' \) -print
     ```

6. **Update the registry**
   - Use `scripts/registry.py upsert` to update the same row with the confirmed live ports, unit, app dir, Nginx config, health check, security posture, update date, and notes about persistent paths.
   - Run registry commands as the deployment/login user. Only use `sudo` for systemd, Nginx, firewall, or filesystem paths that require it.

## Resources

- `~/server-deployments.md` on the target server: mutable registry of deployed projects and port assignments. Always update it after successful deployment or confirmed changes.
- `references/server-deployments-template.md`: template used to initialize the registry when `~/server-deployments.md` is missing.
- `references/nginx-port-mode.md`: Nginx dedicated-port examples and validation commands.
- `references/update-existing-deployment.md`: safe update, rollback, and cleanup procedure for existing deployments.
- `scripts/registry.py`: list, suggest free ports, and upsert project rows in the registry.
- `scripts/render_nginx.py`: generate a dedicated-port Nginx server block.

## Final Response Checklist

Report:

- Deployed project name and server.
- User URL, Nginx listen port, backend bind/port.
- systemd unit and Nginx config path.
- Verification commands and results.
- Cleanup performed for old versions and stale artifacts.
- Registry update path.
- **Security measures applied** (auth method, API docs disabled, sensitive fields masked, bind address) and **residual risks** — see Security Baseline. This is mandatory in every deployment report.
- Any assumptions, missing env vars, firewall/security-group actions still needed, or residual risk.
