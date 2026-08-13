# 前端挂载说明

页面文件已经准备好，并在现有 FastAPI `app.py` 中补充了最小静态挂载与首页路由：

- `templates/index.html`
- `static/style.css`
- `static/app.js`

`app.py` 中对应的挂载内容如下，已保留原有 lifespan、监控逻辑和 API：

```python
from pathlib import Path
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")
```

前端默认每 60 秒请求 `GET /api/status`。当前 FastAPI API 返回 `targets` 数组，页面会为每个目标渲染独立状态行。页面会读取 `latest.resolved_ip`、`latest.checked_at` 以及 `history[*].status` 等现有字段。

```json
{
  "targets": [{
    "host": "ddns.example.com",
    "port": 443,
    "latest": {"resolved_ip": "192.0.2.42", "status": "normal", "checked_at": "2026-08-05T09:42:00+08:00"},
    "history": [{"status": "normal"}, {"status": "blocked"}, {"status": "unknown"}]
  }]
}
```

`history` 取最近 60 次，`status` 支持后端现有的 `normal`、`blocked`、`unknown`；API 暂不可用时页面会显示可操作的演示数据，并明确标注连接状态。
