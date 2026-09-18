# Nginx 入口、HTTPS 与验证

## 选择入口

沿用已有部署方式。有可用域名时优先在 80/443 上按 server_name 路由；多个域名共享端口是正常情况，检查冲突时还要看同一个 server_name 是否已被使用。没有域名路由需求时，选空闲的独立端口。把用户访问端口、Nginx 监听端口和后端服务端口分别记录；存在负载均衡/NAT 时它们可能不同。

后端通常绑定 127.0.0.1。容器服务在容器里可绑定 0.0.0.0，但宿主机发布端口应限制为 127.0.0.1:18001:8000；不能因容器内监听需要而顺便向公网发布后端。

## 配置生成器

在技能目录中生成 HTTP 草稿：

    python3 scripts/render_nginx.py --project translator \
      --public-port 12001 --backend-port 18001 --out /tmp/translator.conf

HTTPS 草稿（先确认域名解析正确、证书文件存在且有效）：

    python3 scripts/render_nginx.py --project translator \
      --server-name translate.example.com --backend-port 18001 \
      --https --redirect-http --out /tmp/translator.conf

自定义 HTTPS 端口可加 --public-port 8443；80 端口跳转会保留 :8443。默认使用 /etc/letsencrypt/live/<domain>/ 下的 fullchain.pem 与 privkey.pem；使用通配符证书或其他 CA 时显式提供 --ssl-certificate 和 --ssl-certificate-key。

生成器仅接受单域名或 IPv4/默认站点占位符，后端支持 IPv4 和 IPv6 地址；复杂 server_name、多上游、子路径部署按现有配置手工审查。生成器不会自动写入服务器目录、开放防火墙、添加认证或生成证书。

普通请求保留传入的 Host（含用户指定端口），并设置 X-Forwarded-*。应用只应信任受控代理传入的转发信息；不能无条件信任任意公网客户端提交的同名头。负载均衡/NAT、多级代理的外部协议和端口需单独核对。

- WebSocket 使用 --websocket，生成升级头及按项目命名的 map。配置须置于 Nginx 的 http 上下文（通常是 conf.d），不要粘到已有 server 块里。
- SSE/模型流式响应使用 --streaming，仅关闭响应缓冲，不添加 WebSocket Upgrade 头。超时按应用最长合法请求设置，例如 --timeout 300。
- HTTP/2 未在草稿中默认启用，避免不同 Nginx 版本语法差异；需要时查目标版本文档后添加。
- 同一项目在多个文件生成 WebSocket map 时需合并/避免重名，不重复 include 同一配置。

## 首次申请证书的顺序

不要先启用引用不存在证书的 TLS 配置，再运行需要有效 Nginx 配置的 Certbot。

1. 核实 DNS 的 A/AAAA 指向实际服务器，以及目标域名没有被其他应用使用。检查云安全组/防火墙；HTTP-01 需要可访问的 80 端口。
2. 准备能通过 nginx -t 的 HTTP 域名配置；证书签发前不要开放需要保护的业务。可只提供 ACME challenge，其余返回临时维护响应。
3. 使用已安装并核实的 Certbot 插件申请证书。例如使用 Nginx 插件时：

       sudo certbot --nginx -d translate.example.com

4. 核对 Certbot 的改动和证书路径，再决定沿用其配置还是安装审查后的生成器草稿；不要重复生成冲突的域名块。
5. 检查自动续期任务和 reload 行为；按现有策略做续期验证。不能因一次签发成功就假定续期正常。

通配符证书需要 DNS-01 及对应 DNS 自动化插件；只在任务确实需要时配置，不能为了普通单域名部署引入 DNS API 权限。DNS API 凭据按凭据规范存储，不打印或放仓库。

## 安装和恢复

先备份现有文件，确认 /tmp 草稿中的所有路径、端口及访问控制；按现有 conf.d 或 sites-enabled 约定安装。执行 nginx -t 成功后 reload，检查实际效果。若失败，先恢复原文件（新增站点则撤掉新文件），再验证旧配置，不能留下会导致下次启动失败的坏文件。

服务器没有既有惯例时，配置路径可选 /etc/nginx/conf.d/<project>.conf。避免同时在 conf.d 和 sites-enabled include 同一站点。仅开放必要的 Nginx 公共端口，后端不用加公网规则。

## 验证实际入口

假设项目有 /health 健康接口，返回可识别的预期内容：

    curl --fail-with-body --silent --show-error --max-time 10 \
      http://127.0.0.1:18001/health

HTTP 独立端口：

    curl --fail-with-body --silent --show-error --max-time 10 \
      http://127.0.0.1:12001/health

HTTPS 域名在本机验证（同时验证 Host、SNI 和证书）：

    curl --fail-with-body --silent --show-error --max-time 10 \
      --resolve translate.example.com:443:127.0.0.1 \
      https://translate.example.com/health

自定义 HTTPS 端口必须同时改 --resolve 和 URL 中的端口。旧版 curl 不支持 --fail-with-body 时改用 --fail。不能用 curl http://127.0.0.1:443，也不能依赖 -k 绕过证书验证。根据接口语义核对状态、内容、版本；跳转及登录响应需要另行验证。

最后从服务器外访问用户实际 URL，检查公网 DNS、防火墙和浏览器行为；服务器本机成功不代表外部可达。日志、响应及配置检查必须避免输出真实凭据。

参考：[Nginx 代理模块](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)、[WebSocket 代理](https://nginx.org/en/docs/http/websocket.html)、[Certbot 官方文档](https://eff-certbot.readthedocs.io/en/stable/using.html)。
