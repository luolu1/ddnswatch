from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import load_config
from monitor import Monitor, TelegramBotService


CONFIG_PATH = os.getenv("DDNSWATCH_CONFIG", "config.yaml")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config(CONFIG_PATH)
    monitor = Monitor(config)
    app.state.monitor = monitor
    telegram = TelegramBotService(config.telegram)
    await telegram.startup(monitor)
    task = asyncio.create_task(monitor.run_forever()) if config.targets else None
    bot_task = asyncio.create_task(telegram.run_forever(monitor)) if telegram.enabled and config.telegram.poll_commands else None
    try:
        yield
    finally:
        monitor.stop()
        await telegram.shutdown()
        if bot_task:
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="DDNSWatch", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
async def api_status() -> dict:
    return app.state.monitor.api_status()
