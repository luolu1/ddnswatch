# DDNSWatch

DDNSWatch 是一个 Cloudflare Worker，用于从中国大陆探测点检查指定 TCP
端点是否可达。它是可达性监控工具，不是 DDNS 更新器。本项目不会创建、修改或
同步任何 DNS 记录。

DDNSWatch is a Cloudflare Worker that checks whether configured TCP endpoints
are reachable from mainland China probes. It is a reachability monitor, not a
DDNS updater, and it does not change DNS records.

## 工作方式

Worker 在每分钟一次的 Cron 触发时检查所有目标，同时为网页和 API 提供实时状态。
每次状态请求都会执行新的检查，不会读取之前的结果。

检查通过 [`tcp.ping.pe`](https://tcp.ping.pe) 的网页流程完成。该流程不是稳定的
公开 API。网站改版、反机器人限制或网络变化都可能产生 `unknown`，也可能使检查
失效。中国大陆 TCP 探测结果只是一项运行信号，不代表所有网络都可达，也不是
ICMP 或 HTTP 检查。

Telegram 通知是可选功能。启用后，只有 Cron 本轮得到 `blocked` 或 `unknown`
结果时才会发送通知。访问 HTTP 状态接口不会发送消息，也没有启动、恢复、轮询或
手动刷新通知。

## 路由

- `GET /` 和其他非 API 路径由 `public/` 中的静态资源提供。
- `GET /health` 返回 `{"status":"ok"}`。
- `GET /api/status` 执行一次新检查并返回当前结果。
- 其他 `/api/*` 路径返回 `404`。
- 本地 Wrangler 开发服务器中的 Cron 测试地址是
  `/cdn-cgi/handler/scheduled?format=json`。

## 源码部署前置条件

如果只使用下一节的 ZIP 上传，可以跳过本节，不需要安装任何工具。

- 一个可以部署 Workers 的 Cloudflare 账号。
- [Bun](https://bun.sh/)，项目在 `package.json` 中固定为 `bun@1.3.14`。
- 首次使用 CLI 部署时，需要浏览器登录 Cloudflare。
- 如需 Telegram 通知，需要由 `@BotFather` 创建的 Bot token，以及接收消息的
  chat ID。不要把真实凭据写入仓库。

## 最简单部署：直接上传 ZIP

不需要 GitHub、Bun、Wrangler 或命令行。直接下载仓库中的：

- [`release/ddnswatch-cloudflare-upload.zip`](release/ddnswatch-cloudflare-upload.zip)
- 完整 Worker 单文件：[`release/_worker.js`](release/_worker.js)

然后在 Cloudflare Dashboard 中执行：

1. 进入 **Workers & Pages**。
2. 选择创建应用，然后选择 **Pages / Direct Upload / Drag and drop**。
3. 输入项目名称，例如 `ddnswatch`。
4. 上传 `ddnswatch-cloudflare-upload.zip` 并部署。
5. 在项目的 Variables and Secrets 中添加下面三项配置。

普通变量：

| 名称 | 示例值 |
| --- | --- |
| `MONITOR_CONFIG_JSON` | `{"check_interval_seconds":60,"targets":[{"name":"网站 HTTPS","host":"example.com","port":443}],"telegram":{"enabled":true}}` |

加密 Secret：

| 名称 | 值 |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | `@BotFather` 提供的机器人 Token |
| `TELEGRAM_CHAT_ID` | 接收通知的用户、群组或频道 ID |

不需要 Telegram 时，将 JSON 中的 `"enabled":true` 改成 `false`，并且不用添加两个
Telegram secrets。保存变量后重新部署一次 ZIP，或者在 Dashboard 中重试最新部署。

ZIP 顶层只有一个预编译的 `_worker.js`。网页 HTML、CSS、JavaScript、监控逻辑和
依赖已经全部打包在里面，不需要上传其他文件。

> Cloudflare 的 Dashboard ZIP 拖拽入口属于 Pages Direct Upload。它可以运行
> `_worker.js` 和实时 `/api/status`，但不能从 ZIP 创建 Workers Cron Trigger。
> 因此这种最简单部署方式可以使用网页手动检测和每 60 秒刷新，但不会在无人访问时
> 自动执行 Telegram 定时检查。Cloudflare 当前没有能够仅靠 ZIP 同时创建 Worker、
> 变量和 Cron 的 Dashboard 上传格式。需要后台 Cron 时，仍需使用后文的 Wrangler
> 部署，或在支持 Scheduled Handler 的 Worker 部署后手动添加 `* * * * *` Trigger。

如果必须保留每分钟后台检测和 Telegram 定时通知，请在 Cloudflare Workers 在线编辑器
中使用 `release/_worker.js` 创建 Worker，然后配置同样的三个变量，并在 Worker 的
Triggers/Cron Triggers 中添加：

```text
* * * * *
```

Cloudflare Workers Dashboard 当前不提供“上传 ZIP 后同时创建 Worker、静态资源和
Cron”的格式，所以完整定时版仍需要手动添加这一个 Trigger；代码本身不需要修改或
本地构建。

可以用同目录的 `.sha256` 文件校验下载内容：

```bash
cd release
sha256sum -c ddnswatch-cloudflare-upload.zip.sha256
```

先安装锁定的依赖：

```bash
bun install --frozen-lockfile
```

## 生产监控配置

部署前，编辑 [`wrangler.jsonc`](wrangler.jsonc) 中 `vars` 下的
`MONITOR_CONFIG_JSON`。这是一个 JSON 字符串，因此内部双引号需要写成 `\"`。
例如：

```jsonc
"vars": {
  "MONITOR_CONFIG_JSON": "{\"check_interval_seconds\":60,\"targets\":[{\"name\":\"网站 HTTPS\",\"host\":\"example.com\",\"port\":443}],\"telegram\":{\"enabled\":false}}"
}
```

完整配置结构如下。未提供的字段使用这里展示的默认值：

```json
{
  "check_interval_seconds": 60,
  "targets": [
    { "name": "Example HTTPS", "host": "example.com", "port": 443 }
  ],
  "tcp_ping": {
    "base_url": "https://tcp.ping.pe",
    "min_cn_probes": 3,
    "blocked_success_rate": 0.2,
    "timeout_seconds": 20,
    "max_polls": 15,
    "poll_interval_seconds": 3
  },
  "telegram": { "enabled": false }
}
```

每个目标都必须有 `port`，并提供 `host` 或 `domain` 之一。使用 `domain` 时，
Worker 会先通过 Cloudflare DNS over HTTPS 解析 IPv4 地址，再交给
`tcp.ping.pe` 检查。

`check_interval_seconds` 固定为 `60`。一次部署最多支持两个目标，`max_polls`
不能超过 `15`。这些限制用于让最坏情况下的探测流程保持在 Cloudflare Workers
Free 计划的 50 次子请求限制内。

`wrangler.jsonc` 中 `vars` 的内容会进入 Git 版本记录，适合保存非敏感生产配置，
不适合保存任何凭据。修改生产目标后，需要提交该文件，自动部署才能使用新配置。

## 本地开发

复制本地变量示例并编辑目标：

```bash
cp .dev.vars.example .dev.vars
bun install --frozen-lockfile
bunx wrangler dev
```

`.dev.vars` 已被 Git 忽略。开发服务器启动后，通过真实 HTTP 接口验证：

```bash
curl http://localhost:8787/health
curl http://localhost:8787/api/status
curl 'http://localhost:8787/cdn-cgi/handler/scheduled?format=json'
```

最后一个地址会在本地调用 Cron handler。每个目标都要等待 `tcp.ping.pe` 的轮询
流程，因此响应可能较慢。

提交前运行与云端构建相同的检查：

```bash
bun run check
```

也可以生成 Wrangler 的 dry run 构建产物：

```bash
bun run build
```

## 首次通过 CLI 部署

建议先用 CLI 完成第一次部署，确认账号、Worker 名称、变量、静态资源和 Cron 均可
正常工作：

```bash
bunx wrangler login
bun run check
bunx wrangler deploy
```

Wrangler 会部署 `src/index.ts`、`public/` 静态资源、`wrangler.jsonc` 中的普通变量
和 Cron 配置。Worker 名称为 `ddnswatch`。

### 不连接 GitHub，直接上传完整项目

这是本项目最简单的“上传文件部署”方式，不需要配置 Workers Builds，也不需要让
Cloudflare 访问 GitHub。下载或解压项目后，在项目根目录运行：

```bash
bun install --frozen-lockfile
bun run check
bunx wrangler login
bunx wrangler deploy
```

`wrangler deploy` 会自动完成以下工作：

1. 将 TypeScript Worker 打包成可运行的 JavaScript。
2. 上传 `public/` 中的 HTML、JavaScript 和 CSS 静态文件。
3. 创建 `ASSETS` binding，使 Worker 可以返回这些静态文件。
4. 部署 `MONITOR_CONFIG_JSON` 普通变量和每分钟 Cron Trigger。
5. 发布为一个完整的 `ddnswatch` Worker，而不是分别部署前端和后端。

以后修改代码或静态文件后，在同一目录再次运行 `bunx wrangler deploy` 即可覆盖更新。
若启用了 Telegram，首次部署后还要按下一节设置两个 secrets；普通重新部署不会要求
把 secrets 写入本地文件。

### 仅上传静态文件的限制

Cloudflare 可以部署只有静态资源的站点，但这种方式不适用于完整的 DDNSWatch。
单独上传 `public/` 只能显示页面外壳，以下功能都依赖 `src/` 中的 Worker：

- `/api/status` 实时检测接口；
- Cloudflare DNS over HTTPS 解析；
- `tcp.ping.pe` TCP 可达性检测；
- 每分钟 Cron Trigger；
- Telegram 通知。

因此，不要只把 `public/` 拖放成静态站点。需要完整功能时，应使用上面的
`bunx wrangler deploy`，它会把 Worker 与静态资源作为同一个部署上传。

Cloudflare 也提供通过 Workers API 分批上传静态资源、Worker module 和部署版本的
Direct Upload API，但这是面向自定义发布系统的高级接口。对本仓库而言，它不会比
Wrangler 更简单，除非你正在编写自己的部署平台。

### 可选 Telegram secrets

只有当 `MONITOR_CONFIG_JSON` 中 `telegram.enabled` 为 `true` 时，才需要设置：

```json
"telegram": { "enabled": true }
```

机器人配置由三个部分组成：

| 配置 | 保存位置 | 说明 |
| --- | --- | --- |
| `telegram.enabled` | `wrangler.jsonc` 的 `MONITOR_CONFIG_JSON` | 是否启用通知 |
| `TELEGRAM_BOT_TOKEN` | Cloudflare Worker secret | `@BotFather` 提供的机器人 Token |
| `TELEGRAM_CHAT_ID` | Cloudflare Worker secret | 接收通知的用户、群组或频道 ID |

设置两个加密 secrets：

```bash
bunx wrangler secret put TELEGRAM_BOT_TOKEN
bunx wrangler secret put TELEGRAM_CHAT_ID
```

执行第一条命令后粘贴 Bot Token，执行第二条命令后粘贴 Chat ID。设置完成后重新部署：

```bash
bunx wrangler deploy
```

命令会交互式读取值，不要把 token 或 chat ID 放在命令参数、
`MONITOR_CONFIG_JSON` 或 `wrangler.jsonc` 中。也可以在 Cloudflare Dashboard 的
Worker 设置中添加同名 secret。Dashboard 的具体导航文字可能随界面版本变化，
关键要求是将两项配置保存为加密的 Worker secrets，而不是普通文本变量。

每次重新创建 Worker 或部署到不同环境时，都要为对应环境单独设置 secrets。
若 token 泄露，请立即通过 `@BotFather` 撤销并创建新 token。

## Cloudflare Workers Builds 自动部署

Cloudflare Workers Builds 支持 Bun，可以直接连接 Git 仓库并在提交后自动构建和
部署，不需要在本仓库新增 GitHub Actions。

在 Cloudflare Dashboard 中为 `ddnswatch` 配置 Git 仓库连接。界面名称可能更新，
请选择用于连接 Git 仓库或设置 Workers Builds 的入口，并使用以下值：

| 设置 | 值 |
| --- | --- |
| Git 仓库 | `luolu1/ddnswatch` |
| Production branch | `main` |
| Root directory | `/` |
| Build command | `bun install --frozen-lockfile && bun run check` |
| Deploy command | `bunx wrangler deploy` |

授权 Cloudflare 访问 GitHub 仓库后，保存构建设置并触发第一次部署。之后推送到
`main` 会触发生产构建和部署。其他分支是否创建预览部署取决于该项目在 Dashboard
中的分支部署设置。

Workers Builds 会从仓库读取 `wrangler.jsonc`，所以 `vars` 下的
`MONITOR_CONFIG_JSON` 会随代码版本部署。Telegram 凭据不会来自 Git，必须预先在
目标 Worker 中通过 Dashboard 或 Wrangler 配置为 secrets。不要在构建命令或
构建环境的普通文本变量中放入这些凭据。

## 部署后验证

从部署结果中取得 `workers.dev` 地址，或使用映射到此 Worker 的自定义路由，然后
执行：

```bash
curl https://your-worker.example/health
curl https://your-worker.example/api/status
```

检查以下结果：

1. `/health` 返回 HTTP 200 和 `{"status":"ok"}`。
2. `/api/status` 返回已配置目标，并能看到本轮探测状态。
3. 网页能打开并显示与 API 一致的当前状态。
4. 如启用了 Telegram，可等待一次 Cron 运行，并确认 `blocked` 或 `unknown` 时收到
   通知。HTTP 请求本身不会触发通知。

需要查看实时日志时运行：

```bash
bunx wrangler tail
```

保持该命令运行，再访问 `/api/status` 或等待 Cron 触发，即可查看 Worker 异常和
探测流程输出。也可以在 Cloudflare Dashboard 中查看该 Worker 的日志与部署记录。

## Cron 时间

Cron 在 [`wrangler.jsonc`](wrangler.jsonc) 中配置为 `* * * * *`，即每分钟触发
一次。Cloudflare Workers Cron 使用 UTC。修改 Cron 后重新部署，变更最多可能需要
15 分钟才能传播完成。因此刚部署或刚改时间时，不应仅凭几分钟内没有触发就判断
配置失败。

Cron 不依赖公开 HTTP 路由。自定义域名和路由在 Cloudflare 中单独配置，只影响
HTTP 访问。

## 回滚与故障排查

如果 Workers Builds 失败，先查看对应构建记录，确认 Bun 安装、
`bun install --frozen-lockfile`、`bun run check` 和 `bunx wrangler deploy` 中具体失败
的步骤。本地运行相同命令通常可以复现依赖、格式、类型或测试错误。

如果部署成功但接口异常：

1. 用 `/health` 区分 Worker 是否可运行，再用 `/api/status` 检查外部探测流程。
2. 运行 `bunx wrangler tail`，随后重新请求异常接口。
3. 检查已部署版本的 `MONITOR_CONFIG_JSON` 是否为合法 JSON，目标是否不超过两个，
   端口和 `host` 或 `domain` 是否正确。
4. Telegram 不发送时，确认 `telegram.enabled` 为 `true`，两个 secrets 都配置在当前
   Worker 环境中，并确认本轮结果确实是 `blocked` 或 `unknown`。
5. `unknown` 可能来自 `tcp.ping.pe` 页面变化、反机器人限制或网络问题，不一定是
   目标端点故障。

需要回滚时，在 Cloudflare Dashboard 的部署或版本历史中选择最近一个已知可用
版本并执行回滚。回滚后重新检查 `/health`、`/api/status` 和实时日志。由于
`MONITOR_CONFIG_JSON` 在 `wrangler.jsonc` 中受版本控制，长期修复还应在 Git 中还原
或修正配置，再让 `main` 产生一次新的自动部署。不要把 secret 写入 Git 来完成
回滚。

## 无存储限制

本项目没有 R2、KV、D1、Durable Objects、bucket 或其他存储绑定，也不会保存历史
检查、速率数据、状态转换或上次运行结果。刷新网页、调用 API 和 Cron 运行得到的都
是新检查。Worker 重启或部署不会丢失持久数据，因为本项目本来就没有持久数据。

因此，当前版本不能提供历史图表、可用率统计、恢复检测、跨运行去重或基于历史状态
的通知。它也不会保存或更新 DDNS/DNS 记录。
