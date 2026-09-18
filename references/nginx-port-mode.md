# 入口适配、HTTPS 与 Nginx 验证

## 入口适配

先判断项目各组件的协议和访问需求。Nginx 负责接收与转发请求，应用进程仍由 systemd、Compose 等方式管理；安装 Nginx 本身不等于部署完成。

- 普通 HTTP Web/API 默认采用 Nginx；WebSocket 和 SSE 使用各自配置。Docker/Compose 运行的 Web 应用也可以接在 Nginx 后面。
- 已有 Caddy、Traefik、项目自带网关或外部负载均衡能满足用户要求、HTTPS、访问控制及项目硬性条件时，优先沿用，核实真实流量路径、TLS 终止位置和可信转发信息，不重复接管端口或叠加无必要的代理。用户明确要求 Nginx 时，不能擅自用另一入口替代；适配不可行则阻断。
- 项目中的 worker、队列消费者和定时任务若没有 HTTP 入口，不为其增加反向代理、开放端口或虚构健康 URL；按任务结果、日志或实际协议验证。当前登记工具以 Web 服务为主，这些组件的信息记入相关项目的部署备注。
- gRPC、TCP/UDP 及其他特殊协议不能套用当前 HTTP 生成器。Nginx 对 gRPC 和 stream 代理有对应模块，但须核对服务器构建、协议、证书和真实业务，选择项目支持的方式并单独验证。所需能力无法满足时，按技能的适配规则阻断，不以更换入口为由放宽其他要求。

采用非 Nginx Web 入口时，登记使用 `upsert --no-nginx`，清空 Nginx 专属端口和配置字段，在备注中写实际入口、配置路径和验证方式。校验、重载与恢复均按实际入口执行，不运行无关的 Nginx 操作。

## HTTPS 默认策略

1. 公网 Web 入口默认落实 HTTPS，不要求用户另外说“配置 HTTPS”。核验用户提供域名的 A/AAAA 记录、入口归属、访问路径、证书覆盖范围和有效期；优先复用有效证书及现有续期方式，必要时在已授权范围内完成签发和续期配置。没有域名时也先核实可用的证书与验证方案，不直接认定无法使用 TLS。
2. 有域名不等于 DNS 已正确生效。根据实际可用条件核实证书验证方式；不能因一次签发失败或某个端口不可达就认定所有方式不可行，也不能无限重试。下文的证书流程适用于采用 Nginx 的入口。
3. 已有网关或负载均衡终止 TLS 时，验证用户到网关、网关到服务的链路及可信转发配置；本机回环上的 HTTP 后端可以保留。不要为了“每一层都是 HTTPS”无依据改写项目，也不能忽略经过不可信网络的明文链路。
4. 确实暂时无法提供 HTTPS 时，记录具体障碍、已尝试或排除的方式、证据、访问范围和待解决事项。只有未被明确要求必须使用 HTTPS、且不承载登录、私密数据、付费模型调用等敏感能力的入口，才可在核实暴露面可接受后作为 HTTP 例外；否则阻断公网业务开放，继续本机或受限准备与验证。不能把未经客户端信任的自签名证书、关闭证书验证或只修改 URL 当成 HTTPS 验证通过。

## 选择入口

采用 Nginx 时，沿用已有部署方式。有可用域名时优先在 80/443 上按 server_name 路由；多个域名共享端口是正常情况，检查冲突时还要看同一个 server_name 是否已被使用。没有域名路由需求时，选空闲的独立端口。把用户访问端口、Nginx 监听端口和后端服务端口分别记录；存在负载均衡/NAT 时它们可能不同。

后端通常绑定 127.0.0.1。容器服务在容器里可绑定 0.0.0.0，但宿主机发布端口应限制为 127.0.0.1:18001:8000；不能因容器内监听需要而顺便向公网发布后端。

## 配置生成器

在技能目录中生成默认的 HTTPS 部署草稿（启用前确认域名解析正确、证书文件存在且有效）：

    python3 scripts/render_nginx.py --project translator \
      --server-name translate.example.com --backend-port 18001 \
      --https --redirect-http --out /tmp/translator.conf

自定义 HTTPS 端口可加 --public-port 8443；80 端口跳转会保留 :8443。默认使用 /etc/letsencrypt/live/<domain>/ 下的 fullchain.pem 与 privkey.pem；使用通配符证书或其他 CA 时显式提供 --ssl-certificate 和 --ssl-certificate-key。

仅在已确认可以使用 HTTP 的场景生成 HTTP 草稿；证书签发前的验证站点还须按下一节限制业务访问：

    python3 scripts/render_nginx.py --project translator \
      --public-port 12001 --backend-port 18001 --out /tmp/translator.conf

生成器按显式参数输出配置，未传 --https 时生成 HTTP。部署流程必须按上述策略主动选择 HTTPS；该默认由 Agent 执行，不能把底层工具的省略参数当成 HTTP 部署授权。

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

参考：[Nginx 代理模块](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)、[WebSocket 代理](https://nginx.org/en/docs/http/websocket.html)、[gRPC 代理](https://nginx.org/en/docs/http/ngx_http_grpc_module.html)、[TCP/UDP 代理](https://nginx.org/en/docs/stream/ngx_stream_proxy_module.html)、[Certbot 官方文档](https://eff-certbot.readthedocs.io/en/stable/using.html)。
