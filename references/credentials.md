# 凭据存储与接入

适用于第三方 API Key、数据库密码、令牌和服务器应用接入。先读本节的「存储边界」，再根据任务读取初始化、使用或恢复部分。

## 存储边界

采用 age 原生 X25519 加密，Python 标准库脚本负责项目分组、锁、原子写入和启动注入。不实现自创加密算法，也不把中文改成英文才能加密：值支持 UTF-8，大小写、标点、空白和换行全部原样保存。

| 对象 | 多应用服务器建议位置 | 内容与权限 |
|---|---|---|
| 稳定工具 | /usr/local/lib/server-port-deploy/ | root 管理的脚本，应用用户不能修改 |
| 密文库 | /var/lib/server-port-deploy/credentials/ | project/environment.age；目录 0700、文件 0600 |
| 解密密钥 | /etc/server-port-deploy/identity.txt | age identity；父目录 0700、文件 0600，与密文分开备份 |
| 凭据目录 | 部署账号的 ~/server-credentials.md | 中文元数据，权限 0600，不含值 |
| 部署表 | 部署账号的 ~/server-deployments.md | 只记凭据引用与接入方式 |

单用户默认路径分别为 ~/.local/share/server-port-deploy/credentials/ 和 ~/.config/server-port-deploy/identity.txt。多人或多个应用共享服务器时，使用上表的 root 管理布局及专用应用账号。工具生产环境面向 Linux；Windows 上 chmod 不等于配置 ACL，不能把 Windows 本地测试视为权限验证。

**自动解密的界限**：本机无需在线；服务器保有解密能力。root、凭据库管理者、能读取 identity 和密文的人仍能解密。加密不能防止服务器完全失陷，也不能隐藏应用已经收到的值。project/environment 是引用范围，不能代替操作系统权限隔离。同一 Linux 用户下的不同应用不构成可靠隔离。目录仅记录元数据，但其中的项目名称、用途也应限制访问。

不同项目默认使用不同的服务商 Key，以便独立撤销和计费。第一版不自动跨项目引用；如业务明确共用同一个 Key，需要记录关联项目并同步轮换，不能按相似名称自行复用。

SSH 登录本身需要的密钥不能仅存在这台服务器的凭据库，否则首次连接会形成循环依赖；继续使用本机 SSH agent 或既有受保护认证方式。

## 初始化与安装

要求 Python 3.9+、age 和 age-keygen。按目标发行版安装官方 age 包；Ubuntu 可用 apt，其他发行版核对官方发布渠道。安装后确认两个命令均可用。参考 [age 官方项目](https://github.com/FiloSottile/age)。

将技能代码安装到稳定、不可被应用账号改写的位置；下面的 skill_dir 指向已检查的技能源目录：

    sudo install -d -m 0755 /usr/local/lib/server-port-deploy/scripts /usr/local/lib/server-port-deploy/references
    sudo install -m 0644 "$skill_dir"/scripts/*.py /usr/local/lib/server-port-deploy/scripts/
    sudo install -m 0644 "$skill_dir"/references/server-deployments-template.md /usr/local/lib/server-port-deploy/references/
    sudo install -d -m 0700 /var/lib/server-port-deploy/credentials /etc/server-port-deploy

Bash 中可定义下面的调用函数；它只是缩短示例，不包含任何凭据：

    spdcred() {
      sudo /usr/bin/python3 -E /usr/local/lib/server-port-deploy/scripts/credentials.py \
        --store /var/lib/server-port-deploy/credentials \
        --identity /etc/server-port-deploy/identity.txt "$@"
    }
    spdcred init

init 已存在 identity 时保留并检查它；已有密文但缺 identity 时拒绝生成新密钥。保存后立即通过可信备份机制单独备份 identity，不能 cat 到聊天/工具日志。不要对应用账号开放这段命令的任意 sudo 权限：凭据管理员本来就拥有读取和启动能力，不能把脚本参数限制当作多租户访问控制。

## 发现、入库与绑定

1. 从部署配置或代码确认**需要哪些变量**，先检查现有文件的位置、权限和变量名称，不输出值；Agent 不应 dump .env、systemctl show 的 Environment、docker inspect 或完整 compose config。
2. 明确 project、environment、name 和中文用途。标识使用小写字母、数字、短横线或下划线，例如 translator/prod/dashscope-api-key；这是路径约束，值和用途均可包含中文。
3. 来源明确、获得当前任务授权后，使用受保护的 stdin 导入。工具不提供 --value 参数和明文 get 命令；不把值插入 shell 命令、临时脚本、here-document 或参数字符串。
4. 通过显式 --bind 把一个作用域内的指定凭据映射到应用环境变量；缺失、重复绑定、密文损坏或解密失败时不启动应用，也不退回明文 .env。
5. 验证一次最小业务行为，然后生成中文目录，在部署表记录引用、应用账号和注入方式。

人工在服务器终端录入时可以使用隐藏输入，管道内容不会成为命令参数。该例是人工交互流程；Agent 运行时应直接从已授权的配置读取并经 stdin/SSH 通道传入，而不是等待隐藏输入：

    python3 -c 'import getpass,sys; sys.stdout.write(getpass.getpass("请输入凭据："))' | \
      spdcred put --project translator --environment prod --name dashscope-api-key \
        --stdin --purpose "AI 翻译器调用千问模型" --kind server

不要用 echo 导入：它通常附加换行。工具不擅自 strip，因此能发现来源问题，也能保存确实需要空白的值。单值上限 64 KiB；整个项目/环境库上限约 4 MiB，不用于证书归档或大文件。

迁移现有 .env 时使用项目本身的 dotenv 解析方式；不要 source 不可信文件，也不要用简单 split 错误处理引号或多行值。先验证新启动方式，再按项目约定移除旧明文副本；这一步不能顺便删除未知配置。

元数据与存在性检查：

    spdcred list --project translator --environment prod
    spdcred check --project translator --environment prod --name dashscope-api-key

check 只说明存在且可解密，不能判断额度、过期、服务商权限或网络可达性。

生成部署账号可读的中文目录（deploy_home 需先核实为真实部署账号家目录；不要重用 HOME）：

    spdcred catalog --out "$deploy_home/server-credentials.md"
    sudo chown "$deploy_user:$deploy_group" "$deploy_home/server-credentials.md"

catalog 是按需重建的元数据快照，put/轮换后必须刷新；两份 Markdown 不是凭据数据库。用途字段只填非敏感说明；不要将 Key 复制进 --purpose、备注或错误消息。

## 本机 Agent 通过 SSH 使用

先查询目录或 list，再执行需要凭据的工具。以下命令只包含凭据引用；服务器端 tools/check_provider.py 应从 DASHSCOPE_API_KEY 读取并调用服务商，输出状态而非 Key：

    ssh server-a 'sudo /usr/bin/python3 -E /usr/local/lib/server-port-deploy/scripts/credentials.py --store /var/lib/server-port-deploy/credentials --identity /etc/server-port-deploy/identity.txt exec --project translator --environment prod --bind DASHSCOPE_API_KEY=dashscope-api-key --as-user translator -- /srv/translator/current/.venv/bin/python /srv/translator/current/tools/check_provider.py'

接入本机专用工具时，需要决定是否允许把凭据取回本机；本技能默认让消费凭据的命令在服务器执行。禁止用 exec 启动 printenv、env、echo 或自行编写的明文输出程序绕过此约定。exec 的子进程有能力输出自己的环境；启动器无法自动识别、脱敏任意业务日志。

## 服务器 AI 应用：systemd 启动注入

适用于能从环境变量读取 Key 的应用。先创建/确认专用 translator 用户和它的应用目录权限，再把以下示例按项目实际启动命令调整。无需修改 SDK 的读 Key 方式；没有环境变量接入能力的应用仍需适配代码。

    [Unit]
    Description=AI 翻译服务
    After=network-online.target
    Wants=network-online.target

    [Service]
    Type=simple
    User=root
    WorkingDirectory=/srv/translator/current
    ExecStart=/usr/bin/python3 -E /usr/local/lib/server-port-deploy/scripts/credentials.py --store /var/lib/server-port-deploy/credentials --identity /etc/server-port-deploy/identity.txt exec --project translator --environment prod --bind DASHSCOPE_API_KEY=dashscope-api-key --as-user translator -- /srv/translator/current/.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 18001
    Restart=on-failure
    RestartSec=3
    UMask=0077
    NoNewPrivileges=true
    LimitCORE=0

    [Install]
    WantedBy=multi-user.target

这里 User=root 只用于受信任的启动脚本读取 root 的库；**--as-user translator 必不可少**。脚本在运行应用前设置应用用户的附加组、GID、UID，再用 exec 替换自身，因此应用进程实际以 translator 身份运行，systemd 信号直接送达它。不要把可由应用用户修改的 Python 文件、PYTHONPATH 或工具路径放进 root 启动阶段。

部署后检查实际主进程用户为 translator，且该账号读不到 identity/其他项目密文；只检查 unit 的 User= 行会误判。一个服务使用一个作用域，--bind 只列出需要的值。非敏感配置继续遵循项目约定，不能在 Environment= 行里复制秘密。

如采用单账号布局，直接以该账号执行 exec 可省略 --as-user；同时必须明确记录同账号应用能访问其凭据库的限制。OS 权限检查不是能力隔离系统，不应宣称这一模式能隔离恶意应用。

## Docker Compose 接入

可以用 exec 给 docker compose 进程注入变量，然后由 Compose 的 environment 显式传给指定服务。例如 compose 文件：

    services:
      translator:
        image: your-translator-image
        environment:
          - DASHSCOPE_API_KEY
        ports:
          - "127.0.0.1:18001:8000"

调用凭据启动器 exec，末尾命令改为经核实的 docker compose -f /srv/translator/compose.yaml up -d translator。需要有 Docker 操作权限的部署账号；不要把 Docker socket 暴露给应用。

这种兼容模式会把明文保存在容器运行配置中，Docker 管理者可通过 inspect 读取；并非全程只在内存。不能接受该限制时，按应用能力改用只读 secret 文件或外部密钥服务，单独评审其权限和生命周期。不要声称普通 Compose secrets 自动提供加密存储。

## 高德与千问的类型区分

| 凭据 | 建议类型 | 接入说明 |
|---|---|---|
| 千问/DashScope 长期 API Key | server | 后端环境变量 DASHSCOPE_API_KEY；不放入浏览器 |
| 高德 Web 服务 Key | server | 服务端调用；按实际账号设置来源/权限限制 |
| 高德 JS API Key | browser-public | 用于浏览器的公开标识，配置平台要求的域名限制；不能当成后端秘密 |
| 高德 securityJsCode | server | 生产环境优先按高德方案用服务端代理配置，不能仅靠前端混淆 |

kind 只是元数据标注，不会自动替服务商配置白名单。参考 [千问 API Key 文档](https://help.aliyun.com/zh/model-studio/get-api-key) 和 [高德安全密钥文档](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)。其他账号/区域的变量名和端点按项目实际文档核实。

## 轮换、备份与恢复

轮换必须先在服务商获得新凭据，工具不会替你生成有效的服务商 Key：

1. 备份现有密文。确认服务商允许新旧 Key 的重叠使用窗口。
2. 通过同一 put 命令追加 --replace，从 stdin 写入新值。未给 --replace 的同名导入会拒绝覆盖；省略用途/类型时保留旧元数据。
3. systemd 重启/滚动替换应用；Compose 需重新创建对应容器。已经运行的进程不会自动更新环境变量。
4. 验证真实业务和日志，刷新中文目录、部署记录；验证成功后按计划撤销旧 Key。
5. 失败先恢复之前的密文并重启，前提是旧 Key 仍有效。恢复备份无法撤销服务商的失效操作。

只备份密文到一个新目录：

    spdcred backup --out /secure-backups/server-credentials/2026-01-01

该命令不复制 identity；目的路径必须在库外，且不能已经存在。实际备份日期和保留策略按服务器规范。密钥通过另一条可信备份途径保存，不打印、不与密文放同一个可下载归档。两者同时进入完整服务器快照时，快照持有者可能解密。

恢复时：暂停写入，在**新目录**恢复原来的 project/environment.age 层级，目录 0700、文件 0600；将对应 identity 恢复到独立受限路径。用显式 --store 和 --identity 执行 list/check，核对引用，再调整服务并进行业务验证。确认前不要覆盖当前可用库。identity 遗失且没有备份就无法解密；有密文时绝不运行新密钥替换操作来“修复”。

第一版不提供自动修改 age identity 的 rekey 命令、多主机同步或审计服务器；需要时选择成熟的密钥管理服务。应用代码升级和 rsync 清理都必须排除整个凭据库及 identity 路径。
