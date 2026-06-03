# Nginx Dedicated-Port Mode

Use this when users access each project as `http://SERVER_IP:PUBLIC_PORT`.

## Request Flow

```text
Browser -> SERVER_IP:PUBLIC_PORT
        -> Nginx server { listen PUBLIC_PORT; }
        -> proxy_pass http://127.0.0.1:BACKEND_PORT
        -> app process
```

## Minimal Server Block

```nginx
server {
    listen PUBLIC_PORT;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:BACKEND_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 10s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
}
```

## WebSocket Variant

Add these headers if the app uses WebSocket, server-sent events, or streaming:

```nginx
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_buffering off;
```

## Install Pattern

```bash
sudo tee /etc/nginx/conf.d/PROJECT.conf >/dev/null <<'NGINX'
server {
    listen PUBLIC_PORT;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:BACKEND_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
}
NGINX

sudo nginx -t
sudo systemctl reload nginx
```

## Conflict Checks

```bash
ss -ltnp | grep -E ':(PUBLIC_PORT|BACKEND_PORT)\b' || true
sudo nginx -T | grep -nE 'listen\s+PUBLIC_PORT\b|proxy_pass\s+http://127\.0\.0\.1:BACKEND_PORT\b' || true
```

Also check cloud security groups and host firewall:

```bash
sudo ufw status numbered 2>/dev/null || true
sudo firewall-cmd --list-ports 2>/dev/null || true
```

## Common Failure Modes

- Public port is open in Nginx but blocked by cloud security group.
- App binds `127.0.0.1` on a different backend port than Nginx uses.
- App binds `0.0.0.0` and is directly exposed, bypassing Nginx.
- Nginx config validates but old config still handles the port because another `listen` block also matches it.
- Long-running extraction or build endpoints time out because `proxy_read_timeout` is too low.
