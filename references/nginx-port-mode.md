# Nginx Dedicated-Port Mode

Use this when users access a project through Nginx as `http://SERVER_IP/`, `https://DOMAIN/`, or `http://SERVER_IP:PUBLIC_PORT` for additional dedicated ports.

Default to public port `80` for HTTP when it is free. Use public port `443` for HTTPS when a domain points to the server and a TLS certificate is available. Use `12001-12999` for additional independent projects on the same IP when domain/path routing is not being used.

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

## HTTPS Variant

HTTPS on `443` is straightforward when the user has a domain pointing to the server. A normal public TLS certificate is domain-based; HTTPS for a bare IP usually requires a self-signed or special certificate and browsers will warn.

For per-tool subdomains such as `<tool>.huyujie.top`, use shared ports `80` and `443` with one Nginx `server_name` block per tool. Each block proxies to a different local backend port, for example:

```text
tool-a.huyujie.top -> Nginx 443 -> 127.0.0.1:18001
tool-b.huyujie.top -> Nginx 443 -> 127.0.0.1:18002
```

Typical flow:

```bash
sudo certbot --nginx -d DOMAIN
sudo nginx -t
sudo systemctl reload nginx
```

Run Certbot once for each new hostname when using per-subdomain certificates. Certbot installs renewal; do not rerun it for normal app updates or restarts.

For many subdomains, prefer a wildcard certificate if the DNS provider supports API automation:

```bash
sudo certbot certonly --dns-<provider> -d huyujie.top -d '*.huyujie.top'
```

With a wildcard certificate, add a new Nginx `server_name` block for each tool and reuse the wildcard certificate paths. You still reload Nginx for each new tool, but you do not need to issue a new certificate every time.

Manual server block shape:

```nginx
server {
    listen 80;
    server_name DOMAIN;

    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name DOMAIN;

    ssl_certificate /etc/letsencrypt/live/DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/DOMAIN/privkey.pem;

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
