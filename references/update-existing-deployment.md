# Updating an Existing Dedicated-Port Deployment

Use this when the project already exists on the server and the user wants a new version deployed under the same public port rules.

## Goals

- Preserve the existing user URL, Nginx listen port, backend port, service name, and registry row unless the user asks to change them.
- Stop the old process cleanly before replacing runtime files.
- Keep declared persistent state only: `.env`, uploaded files, databases, logs that must be retained, and explicit shared storage.
- Remove obsolete app code, stale build output, temporary archives, old virtualenvs, old node modules, and unused release directories after the new version is verified.
- Leave Nginx, systemd, and `~/server-deployments.md` matching the new live state.

## Discovery

1. Read `~/server-deployments.md` as the normal deployment user.
2. Query the existing row when possible:

   ```bash
   python scripts/registry.py get --project "<project>" --server "<server>"
   ```

3. Verify live state before making changes:

   ```bash
   systemctl cat <unit>
   systemctl status <unit> --no-pager
   sudo nginx -T | grep -nE 'listen\s+<public_port>\b|proxy_pass\s+http://127\.0\.0\.1:<backend_port>\b' || true
   ss -ltnp | grep -E ':(<public_port>|<backend_port>)\b' || true
   ls -la <app_dir>
   ```

4. Compare the registry row with live systemd and Nginx config. Trust live state for safety, then update the registry after successful verification.

## Update Strategy

Prefer a clean replacement that preserves only explicit shared state.

For simple flat deployments:

```bash
sudo systemctl stop <unit>
sudo rsync -a --delete \
  --exclude '.env' \
  --exclude '.venv' \
  --exclude 'node_modules' \
  --exclude 'data' \
  --exclude 'uploads' \
  --exclude 'storage' \
  --exclude 'logs' \
  <new_project_dir>/ <app_dir>/
```

Then recreate dependencies/build outputs from the new source, rather than reusing stale dependency folders:

```bash
cd <app_dir>
# Python example
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

# Node example
npm ci
npm run build
```

For projects with meaningful downtime or rollback needs, use a release layout:

```text
<base_dir>/
  current -> releases/<timestamp>
  releases/<timestamp>/
  shared/.env
  shared/data/
  shared/uploads/
```

Build the new release first, repoint `current`, restart, verify, then delete old release directories after success unless the user explicitly asks to keep rollback copies.

## Systemd and Nginx

- Reuse the existing unit name unless the current name is clearly wrong.
- If the start command, working directory, environment path, or backend port changed, update the unit file and run:

  ```bash
  sudo systemctl daemon-reload
  ```

- Keep app services bound to `127.0.0.1:<backend_port>`.
- If Nginx config changes, back up the old file before overwriting:

  ```bash
  sudo cp <nginx_conf> <nginx_conf>.bak.$(date +%Y%m%d-%H%M%S)
  sudo nginx -t
  sudo systemctl reload nginx
  ```

- After Nginx reload succeeds and the public URL is verified, remove superseded `.bak.*` files created during this update unless retaining them is needed for an active rollback.

## Restart and Verification

Use this order:

```bash
sudo systemctl start <unit>
systemctl status <unit> --no-pager
journalctl -u <unit> -n 80 --no-pager
curl -i http://127.0.0.1:<backend_port>/<health_path>
curl -i http://127.0.0.1:<public_port>/<health_path>
```

If verification fails:

- Stop the failed unit.
- Restore the previous systemd/Nginx files or previous release symlink if a backup exists.
- Run `sudo systemctl daemon-reload`, `sudo nginx -t`, reload Nginx if needed, and start the old service.
- Report the failure and leave the registry pointing at the working live version.

## Cleanup Standard

After successful verification:

- Remove temporary upload archives and extracted staging directories.
- Remove stale build outputs that are not part of the active version.
- Remove old release directories unless the user explicitly requested rollback retention.
- Remove Nginx backup files created during this update after the new config is verified.
- Keep only persistent state that the project requires and that is documented in the registry notes.
- Run a final check:

  ```bash
  systemctl is-active <unit>
  ss -ltnp | grep -E ':(<public_port>|<backend_port>)\b' || true
  find <app_dir> -maxdepth 2 \( -name '*.bak.*' -o -name '*.tmp' -o -name '__pycache__' \) -print
  ```

Do not delete databases, uploads, `.env`, user content, or declared shared storage unless the user explicitly asks.

## Registry Update

Update the existing row with the confirmed live state:

```bash
python scripts/registry.py upsert \
  --project "<project>" \
  --server "<server>" \
  --user-url "http://<server_ip>:<public_port>" \
  --user-port <public_port> \
  --nginx-port <public_port> \
  --backend-bind "127.0.0.1" \
  --backend-port <backend_port> \
  --process-manager "systemd" \
  --unit "<unit>" \
  --app-dir "<app_dir>" \
  --health-check "http://127.0.0.1:<backend_port>/<health_path>" \
  --nginx-config "<nginx_conf>" \
  --notes "updated to <version>; persistent state: <paths>"
```

Run this as the deployment/login user, not through `sudo`, so the registry remains at `~/server-deployments.md`.
