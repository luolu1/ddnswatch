# DDNSWatch

DDNSWatch 是一个基于 FastAPI 的主机/域名可达性监控服务，用于观察目标 TCP 端口从中国大陆探针访问时的运行信号，并通过网页和 Telegram 提供状态。

DDNSWatch is a FastAPI service for monitoring host/domain TCP reachability signals from mainland-China probes, with a web console and Telegram notifications.

## 功能 / Features

- YAML 配置监控目标，支持 `host` 或 `domain`、端口和自定义名称。
- 定时检测（默认 25 秒；应用限制为 20–30 秒），域名会解析为 IPv4 并记录实际检测 IP。
- SQLite 保存原始检测结果，并提供最近 60 个 UTC 分钟桶、最新状态和正常率。
- 状态从 `normal` 变为 `blocked`，或从 `blocked` 恢复为 `normal` 时发送 Telegram 通知。
- Telegram Bot 菜单提供“查看全部状态”“立即检测”“帮助”，也支持 `/start`、`/status`、`/refresh`、`/help`。
- 启动时可发送“DDNSWatch 已上线”通知；只接受配置的 `chat_id`。
- YAML configuration for named host/domain targets, ports, intervals, and Telegram.
- SQLite history, a health endpoint, a web console, and a JSON status API.
- Telegram menu, startup/上线 notification, blocked/recovery notifications, and manual refresh commands.

## 检测限制 / Detection limitations

检测使用 `tcp.ping.pe` 的浏览器网页流程（cookie、动态 token、轮询等），这是**非官方网页协议**，不是稳定的公开 API。网站改版、反爬策略或网络变化都可能使检测返回 `unknown` 或失效。

This project uses the browser flow of `tcp.ping.pe`. It is an **unofficial webpage protocol**, not a stable public API, and may change or trigger anti-bot protection. A mainland TCP probe is an operational signal, not absolute proof of reachability from every Chinese network, nor the same as ICMP or HTTP reachability.

## Docker Compose 快速部署 / Quick deployment

Docker 镜像构建时不会复制真实配置；首次启动时如果 `data/config.yaml` 不存在，入口脚本会自动从 `config.example.yaml` 创建它。配置和数据库都放在宿主机的 `data/` 目录。首次部署：

```bash
mkdir -p data
cp config.example.yaml data/config.yaml
docker compose up -d --build
```

The Compose topology builds a local `warp-proxy` gateway. On its first start it
registers a free consumer WARP account, generates a profile, removes IPv6
routes/addresses, creates WireProxy HTTP/SOCKS5 listeners, validates the
configuration, and starts WireProxy. The derived profile replaces mixed DNS
entries with the IPv4 resolver `1.1.1.1` and removes IPv6 routes and addresses. Credentials persist in the named
`warp-state` volume, so later starts reuse healthy state. A bounded internal
supervisor tolerates transient WireProxy `/readyz` failures and rotates the
account only after sustained failures, with a persisted cooldown and capped
registration backoff. The build pins
wgcf `v2.2.29` and WireProxy `v1.1.2` and supports amd64/arm64. No manual
`warp.conf`, host-specific detection, fscarmen script, TUN, or privileged mode
is required. See [`warp-proxy/README.md`](warp-proxy/README.md) for details.

Compose 会在本地构建 `warp-proxy` 网关。首次启动时，网关自动接受 WARP
服务条款并注册免费消费者账户，生成配置后移除 IPv6 地址和 `::/0` 路由，
再启动仅供 Compose 内部使用的 HTTP/SOCKS5 代理。账户和密钥保存在
`warp-state` 命名卷中，后续重启会直接复用，不会重复注册。wgcf 是非官方
客户端，Cloudflare 接口变化、限流或停止兼容都可能导致首次注册失败。

验证 WARP IPv4 出口：

```bash
docker compose exec ddnswatch python -c \
  "import httpx; print(httpx.get('https://www.cloudflare.com/cdn-cgi/trace', timeout=10, trust_env=True).text)"
```

该命令与应用使用相同的 httpx 环境代理设置；输出应包含 `warp=on`。它验证 HTTPS
请求经过 WireProxy/WARP；它不会证明 libc DNS 被代理。DDNSWatch 对监控域名调用的
`getaddrinfo` 仍使用容器的普通 DNS 解析路径。

如需删除旧凭据并重新注册：

```bash
docker compose down -v
docker compose up -d --build
```

Compose 会将数据库固定写入 `/app/data/ddnswatch.sqlite3`。容器默认以 root 运行，以兼容 Docker 首次创建的 root-owned bind mount；如果你希望使用非 root 运行方式，请先将宿主机目录授权给 UID/GID `10001`，并在 Compose 中覆盖用户配置：

```bash
sudo chown -R 10001:10001 data
```

然后在 `docker-compose.yml` 的服务中增加 `user: "10001:10001"` 即可使用非 root 模式。`docker-compose.yml` 挂载 `./data:/app/data`，设置 `DDNSWATCH_CONFIG=/app/data/config.yaml` 和 `DDNSWATCH_DATABASE_PATH=/app/data/ddnswatch.sqlite3`。可用 `DDNSWATCH_PORT` 修改对外端口。

The image never copies a real configuration. On first startup, the entrypoint creates `data/config.yaml` from the example when it is missing. `./data` is mounted at `/app/data`, and the Compose environment explicitly stores SQLite at `/app/data/ddnswatch.sqlite3`. The default root mode avoids first-run bind-mount permission failures; use a pre-owned directory and UID/GID `10001:10001` if you require non-root execution.

All outbound HTTP(S) calls made by ddnswatch, including the tcp.ping.pe browser
flow and Telegram Bot API, use the sidecar through `HTTP_PROXY`, `HTTPS_PROXY`,
and `ALL_PROXY` (uppercase and lowercase forms). Every production httpx client
sets `trust_env=True`. Loopback destinations are excluded with `NO_PROXY`. DNS
resolver behavior is unchanged: libc DNS itself is not proxied, and monitored
domains are still resolved locally to IPv4 before tcp.ping.pe is called.

## 手动 Python 部署 / Manual Python deployment

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
uvicorn app:app --host 0.0.0.0 --port 8000
```

手动运行时默认读取项目根目录的 `config.yaml`，也可以设置 `DDNSWATCH_CONFIG`。不要把包含真实 token 的 `config.yaml` 提交到 Git。

For a manual install, create a virtual environment, copy the example configuration, and run Uvicorn as shown above. Never commit a real configuration, bot token, or other secret to Git.

## Telegram 配置 / Telegram setup

1. 在 Telegram 联系 `@BotFather` 创建 Bot，取得 bot token。
2. Compose 部署时编辑 `data/config.yaml`，将 `telegram.enabled` 改为 `true`，填写 `bot_token` 和目标会话的 `chat_id`。
3. `poll_commands: true` 启用 Bot 菜单和命令；`startup_notification: true` 启用上线通知。
4. 目标被判定为被墙时发送通知，恢复为正常时发送恢复通知。菜单包含状态、立即检测和帮助。

Set the authorized `chat_id`; updates from other chats are ignored. If a token is ever exposed, immediately revoke it in `@BotFather` and replace it with a newly issued token.

```yaml
telegram:
  enabled: true
  bot_token: "YOUR_TELEGRAM_BOT_TOKEN"
  chat_id: "YOUR_CHAT_ID"
  poll_commands: true
  startup_notification: true
```

## 配置说明 / Configuration

完整模板见 [`config.example.yaml`](config.example.yaml)。`targets` 至少需要 `host`/`domain` 和 `port`；默认检测间隔为 25 秒。请只在本地或 `data/config.yaml` 中填写真实 token，不要修改后提交示例文件中的占位符。

See [`config.example.yaml`](config.example.yaml) for all options. Keep real credentials only in local configuration or `data/config.yaml`; do not commit them.

## 启动、停止、日志、更新 / Operations

```bash
# 启动 / start
docker compose up -d

# 停止并删除容器 / stop and remove containers
docker compose down

# 查看日志 / follow logs
docker compose logs -f ddnswatch

# 更新代码后重新构建并启动 / rebuild after updating code
git pull
docker compose up -d --build
```

## 访问地址与 API / URL and API

- Web console: `http://localhost:8000/`（若设置 `DDNSWATCH_PORT`，使用对应端口）
- `GET /health`：健康检查，返回 `{"status":"ok"}`。
- `GET /api/status`：返回目标最新记录、最近 60 个 UTC 分钟桶、`last_check_at`、`last_status` 和正常率。

The Compose healthcheck calls `http://127.0.0.1:8000/health` inside the container.
The `warp-proxy` service has its own bounded `/readyz` healthcheck, and
ddnswatch waits for that service to become healthy before starting. To verify
IPv4 egress explicitly, use the command above or the details in
[`warp-proxy/README.md`](warp-proxy/README.md).

## 测试 / Tests

```bash
pytest
```

## 数据备份 / Data backup

Compose 的配置和 SQLite 数据均在 `data/`。停止服务后备份可得到一致快照：

```bash
docker compose down
tar -czf ddnswatch-data-$(date +%Y%m%d-%H%M%S).tar.gz data/
docker compose up -d
```

也可只备份 `data/ddnswatch.sqlite3` 和 `data/config.yaml`。备份文件及配置中的真实 token 同样必须妥善保管，切勿提交到 Git。

Both configuration and the SQLite database live under `data/`. Protect backups because they may contain the real Telegram token.
