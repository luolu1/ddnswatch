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

Docker 镜像构建时不会复制真实配置；配置和数据库都放在宿主机的 `data/` 目录。首次部署：

```bash
mkdir -p data && cp config.example.yaml data/config.yaml && docker compose up -d --build
```

容器以非 root 用户 UID/GID `10001` 运行。若宿主机创建的 `data` 目录不可写，请在首次启动前授权：

```bash
sudo chown -R 10001:10001 data
```

`docker-compose.yml` 挂载 `./data:/app/data`，并将 `DDNSWATCH_CONFIG` 设置为 `/app/data/config.yaml`；因此数据库默认写入 `data/ddnswatch.sqlite3`。可用 `DDNSWATCH_PORT` 修改对外端口。

The image never copies a real configuration. `./data` is mounted at `/app/data`, and the container runs as UID/GID `10001`. Make the bind mount writable for that user when needed.

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
