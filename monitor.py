from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import logging
import re
import socket
import sqlite3
from urllib.parse import quote
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
from config import AppConfig, TargetConfig, TcpPingConfig, TelegramConfig


def create_http_client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(trust_env=True, **kwargs)


STATUS_NORMAL = "normal"
STATUS_BLOCKED = "blocked"
STATUS_UNKNOWN = "unknown"


@dataclass(frozen=True)
class CheckResult:
    status: str
    reason: str = ""


class ConnectivityChecker(Protocol):
    async def check(self, target: TargetConfig, resolved_ip: str | None) -> CheckResult:
        ...


class TelegramNotifier:
    def __init__(self, config: TelegramConfig, *, http_client_factory: Callable[..., Any] | None = None):
        self.config = config
        self.http_client_factory = http_client_factory or create_http_client

    def _url(self, method: str) -> str:
        if not self.config.bot_token:
            raise ValueError("Telegram is enabled but bot_token/chat_id is missing")
        return f"https://api.telegram.org/bot{self.config.bot_token}/{method}"

    async def _request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.config.enabled:
            return {}
        if not self.config.bot_token or not self.config.chat_id:
            raise ValueError("Telegram is enabled but bot_token/chat_id is missing")
        async with self.http_client_factory(timeout=10) as client:
            response = await client.post(self._url(method), json=payload)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                raise RuntimeError("Telegram API returned an unsuccessful response")
            return payload

    async def send(self, message: str, reply_markup: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": self.config.chat_id, "text": message}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        await self._request("sendMessage", payload)


class TelegramBotService:
    """Small Telegram Bot API long-polling service for a single authorized chat."""

    COMMANDS = [
        {"command": "start", "description": "显示监控菜单"},
        {"command": "status", "description": "查看全部状态"},
        {"command": "refresh", "description": "立即检测"},
        {"command": "help", "description": "查看帮助"},
    ]
    KEYBOARD = {"keyboard": [["📊 查看全部状态", "🔄 立即检测"], ["ℹ️ 帮助"]], "resize_keyboard": True}

    def __init__(self, config: TelegramConfig, *, http_client_factory: Callable[..., Any] | None = None,
                 notifier: TelegramNotifier | None = None):
        self.config = config
        self.notifier = notifier or TelegramNotifier(config, http_client_factory=http_client_factory)
        self.http_client_factory = http_client_factory or create_http_client
        self._stopped = asyncio.Event()
        self._offset: int | None = None
        self._refresh_task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled and self.config.bot_token and self.config.chat_id)

    def _url(self, method: str) -> str:
        if not self.config.bot_token:
            raise ValueError("Telegram bot_token is missing")
        return f"https://api.telegram.org/bot{self.config.bot_token}/{method}"

    async def _api(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self.http_client_factory(timeout=35) as client:
            response = await client.post(self._url(method), json=payload)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise RuntimeError(f"Telegram API {method} returned an unsuccessful response")
            return data

    async def set_commands(self) -> None:
        if self.enabled:
            await self._api("setMyCommands", {"commands": self.COMMANDS})

    def startup_text(self, monitor: "Monitor") -> str:
        return "\n".join([
            "🚀 DDNSWatch 已上线",
            f"启动时间: {now_utc().isoformat()}",
            f"监控目标: {len(monitor.config.targets)} 个",
            f"检测间隔: {monitor.config.check_interval_seconds} 秒",
            "发送 /start 或使用下方菜单进行交互。",
        ])

    async def startup(self, monitor: "Monitor") -> None:
        if not self.enabled:
            return
        try:
            await self.set_commands()
            if self.config.startup_notification:
                await self.notifier.send(self.startup_text(monitor), self.KEYBOARD)
        except Exception:  # noqa: BLE001 - a bot outage must not stop monitoring.
            logging.exception("Failed to initialize Telegram bot")

    def is_authorized(self, chat_id: object) -> bool:
        return self.config.chat_id is not None and str(chat_id) == str(self.config.chat_id)

    def status_text(self, monitor: "Monitor") -> str:
        status = monitor.api_status()
        status_labels = {
            STATUS_NORMAL: "正常",
            STATUS_BLOCKED: "被墙",
            STATUS_UNKNOWN: "未知",
        }
        lines = ["📊 DDNSWatch 全部状态"]
        for item in status["targets"]:
            latest = item["latest"] or {}
            lines.extend([
                "",
                f"主机: {item['name'] or item['host']} ({item['host']})",
                f"解析 IP: {latest.get('resolved_ip') or '未解析'}",
                f"端口: {item['port']}",
                f"当前状态: {status_labels.get(item['last_status'], '未知')}",
                f"最近一小时正常率: {item['normal_rate_60m']:.2f}%",
                f"最后检测时间: {item['last_check_at'] or '尚未检测'}",
            ])
        return "\n".join(lines)

    @staticmethod
    def help_text() -> str:
        return "ℹ️ 帮助\n📊 查看全部状态：显示所有目标的最新状态。\n🔄 立即检测：立刻执行一次全部检测。\n也可使用 /status、/refresh、/help。"

    @staticmethod
    def refresh_text(results: list[dict]) -> str:
        counts = {state: sum(1 for row in results if row["status"] == state) for state in (STATUS_NORMAL, STATUS_BLOCKED, STATUS_UNKNOWN)}
        details = "、".join(f"{row['target']['host']}:{row['target']['port']}={row['status']}" for row in results)
        return f"🔄 检测完成\n正常: {counts[STATUS_NORMAL]}，被墙: {counts[STATUS_BLOCKED]}，未知: {counts[STATUS_UNKNOWN]}\n{details}"

    async def handle_update(self, update: dict[str, Any], monitor: "Monitor") -> None:
        message = update.get("message")
        if not isinstance(message, dict) or not self.is_authorized((message.get("chat") or {}).get("id")):
            return
        text = message.get("text")
        if not isinstance(text, str):
            return
        text = text.strip()
        command = text.split(maxsplit=1)[0].split("@", 1)[0] if text.startswith("/") else text
        if command in {"/start", "/help", "ℹ️ 帮助"}:
            await self.notifier.send(self.help_text() if command != "/start" else "欢迎使用 DDNSWatch。\n" + self.help_text(), self.KEYBOARD)
        elif command in {"/status", "📊 查看全部状态"}:
            await self.notifier.send(self.status_text(monitor), self.KEYBOARD)
        elif command in {"/refresh", "🔄 立即检测"}:
            if self._refresh_task and not self._refresh_task.done():
                await self.notifier.send("⏳ 检测正在进行中", self.KEYBOARD)
                return
            await self.notifier.send("🔄 已开始检测，请稍候…", self.KEYBOARD)
            self._refresh_task = asyncio.create_task(self._run_refresh(monitor))

    async def _run_refresh(self, monitor: "Monitor") -> None:
        try:
            results = await monitor.check_all_once()
            await self.notifier.send(self.refresh_text(results), self.KEYBOARD)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - do not expose checker details to Telegram.
            logging.exception("Telegram refresh failed")
            try:
                await self.notifier.send("❌ 立即检测失败，请稍后重试", self.KEYBOARD)
            except Exception:  # noqa: BLE001 - notification failures must not escape polling.
                logging.exception("Failed to send Telegram refresh failure message")
        finally:
            if self._refresh_task is asyncio.current_task():
                self._refresh_task = None

    async def run_forever(self, monitor: "Monitor") -> None:
        while not self._stopped.is_set():
            try:
                payload: dict[str, Any] = {"timeout": 30}
                if self._offset is not None:
                    payload["offset"] = self._offset
                response = await self._api("getUpdates", payload)
                updates = response.get("result", [])
                if not isinstance(updates, list):
                    continue
                for update in updates:
                    if isinstance(update, dict):
                        update_id = update.get("update_id")
                        if isinstance(update_id, int):
                            self._offset = update_id + 1
                        await self.handle_update(update, monitor)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - polling must not affect monitor task.
                logging.exception("Telegram polling failed")
                await asyncio.sleep(5)

    def stop(self) -> None:
        self._stopped.set()

    async def shutdown(self) -> None:
        self.stop()
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        self._refresh_task = None


class TcpPingChecker:
    """Use tcp.ping.pe's browser flow as a best-effort fallback signal."""

    MAINLAND_TERMS = (
        "中国", "北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
        "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东",
        "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾", "内蒙古",
        "广西", "西藏", "宁夏", "新疆",
        "China", "china", "中国", "Beijing", "Shanghai", "Guangzhou", "Shenzhen",
        "Hong Kong", "香港", "澳门", "Guangdong", "Zhejiang", "Jiangsu", "Sichuan",
    )

    def __init__(self, config: TcpPingConfig | None = None, *, timeout: float = 20.0,
                 base_url: str = "https://tcp.ping.pe", min_cn_probes: int = 3,
                 blocked_success_rate: float = 0.2, max_polls: int = 20,
                 poll_interval_seconds: float = 3.0, http_client_factory: Callable[..., Any] | None = None,
                 sleep: Callable[[float], Awaitable[None]] | None = None):
        if config:
            timeout, base_url, min_cn_probes = config.timeout_seconds, config.base_url, config.min_cn_probes
            blocked_success_rate, max_polls = config.blocked_success_rate, config.max_polls
            poll_interval_seconds = config.poll_interval_seconds
        self.timeout, self.base_url = timeout, base_url.rstrip("/")
        self.min_cn_probes, self.blocked_success_rate = min_cn_probes, blocked_success_rate
        self.max_polls, self.poll_interval_seconds = max_polls, poll_interval_seconds
        self.http_client_factory = http_client_factory or create_http_client
        self.sleep = sleep or asyncio.sleep

    async def check(self, target: TargetConfig, resolved_ip: str | None) -> CheckResult:
        if not resolved_ip:
            return CheckResult(STATUS_UNKNOWN, "tcp.ping.pe target has no resolved IPv4 address")
        stream_id = None
        headers = {"User-Agent": "Mozilla/5.0 (DDNSWatch; tcp.ping.pe)",
                   "Origin": self.base_url, "X-Requested-With": "XMLHttpRequest"}
        try:
            async with self.http_client_factory(follow_redirects=True, timeout=self.timeout, headers=headers) as client:
                encoded_target = quote(f"{resolved_ip}:{target.port}", safe=".")
                page_url = f"{self.base_url}/{encoded_target}"
                page_headers = {**headers, "Referer": self.base_url + "/"}
                response = await client.get(page_url, headers=page_headers)
                task_query, start_token, interval = extract_tcp_ping_task(response.text)
                if not task_query or not start_token:
                    antiflood = extract_antiflood_cookie(response.text)
                    if antiflood:
                        client.cookies.set("antiflood", antiflood)
                    retry_headers = {**headers, "Referer": page_url}
                    response = await client.get(page_url + "?browsercheck=ok", headers=retry_headers)
                    task_query, start_token, interval = extract_tcp_ping_task(response.text)
                if response.status_code != 200:
                    return CheckResult(STATUS_UNKNOWN, f"tcp.ping.pe browser validation HTTP {response.status_code}")
                if not task_query or not start_token:
                    return CheckResult(STATUS_UNKNOWN, "tcp.ping.pe browser validation/token missing")
                if interval is not None:
                    self.poll_interval_seconds = max(0.0, interval)
                task_headers = {**headers, "Referer": page_url + "?browsercheck=ok"}
                started = await client.post(f"{self.base_url}/ajax_startTask_v1.php",
                                            data={"query": task_query, "start_token": start_token}, headers=task_headers)
                payload = started.json()
                stream_id = payload.get("data", {}).get("stream_id") if isinstance(payload, dict) and payload.get("ok") is True else None
                if not stream_id:
                    return CheckResult(STATUS_UNKNOWN, "tcp.ping.pe task start failed")
                try:
                    return await self._poll(client, stream_id, task_headers)
                finally:
                    try:
                        await client.get(
                            f"{self.base_url}/ajax_stopTask.php?stream_id={quote(str(stream_id), safe='')}",
                            headers=task_headers,
                        )
                    except Exception:
                        pass
        except Exception as exc:  # noqa: BLE001
            return CheckResult(STATUS_UNKNOWN, f"tcp.ping.pe check failure: {exc}")

    async def _poll(self, client: Any, stream_id: str, headers: dict[str, str]) -> CheckResult:
        nodes: dict[str, dict] = {}
        outstanding = None

        def merge_outstanding(metadata: object) -> None:
            if isinstance(metadata, dict):
                entries = ((node_id, node) for node_id, node in metadata.items())
            elif isinstance(metadata, (list, tuple)):
                entries = ((None, node) for node in metadata)
            else:
                return
            for node_id, node in entries:
                if not isinstance(node, dict):
                    continue
                identity = node.get("node_id") or node_id
                if identity is not None:
                    enriched = {"node_id": str(identity), **node}
                    nodes.setdefault(str(identity), {}).update(enriched)

        for poll_number in range(1, self.max_polls + 1):
            response = await client.get(
                f"{self.base_url}/ajax_getPingResults_v2.php?type=tcp&totalPolls={poll_number}&stream_id={quote(str(stream_id), safe='')}",
                headers=headers,
            )
            payload = response.json()
            state = payload.get("state", {}) if isinstance(payload, dict) else {}
            if isinstance(state, dict):
                outstanding = state.get("outstandingNodeCount", outstanding)
                merge_outstanding(state.get("outstandingNodes", []))
            data = payload.get("data", []) if isinstance(payload, dict) else []
            if isinstance(data, dict):
                if isinstance(data.get("state"), dict):
                    nested_state = data["state"]
                    outstanding = nested_state.get("outstandingNodeCount", outstanding)
                    merge_outstanding(nested_state.get("outstandingNodes", []))
                data = data.get("data", [])
            for item in data if isinstance(data, list) else []:
                if isinstance(item, dict):
                    key = str(item.get("node_id", item.get("id", len(nodes))))
                    nodes.setdefault(key, {}).update(item)
            if outstanding == 0:
                return aggregate_tcp_ping_results(list(nodes.values()), self.min_cn_probes, self.blocked_success_rate)
            if outstanding and self._has_enough_complete_cn_results(nodes):
                return aggregate_tcp_ping_results(list(nodes.values()), self.min_cn_probes, self.blocked_success_rate)
            if poll_number < self.max_polls:
                await self.sleep(self.poll_interval_seconds)
        return CheckResult(STATUS_UNKNOWN, "tcp.ping.pe outstanding nodes did not finish")

    def _has_enough_complete_cn_results(self, nodes: dict[str, dict]) -> bool:
        """Allow a partial task when only non-mainland probes remain pending."""
        completed_cn = [node for node in nodes.values() if _tcp_is_mainland(node) and "result" in node]
        pending_cn = [node for node in nodes.values() if _tcp_is_mainland(node) and "result" not in node]
        return len(completed_cn) >= self.min_cn_probes and not pending_cn


def extract_tcp_ping_task(text: str) -> tuple[str | None, str | None, float | None]:
    text = html.unescape(text)
    def value(name: str) -> str | None:
        match = re.search(rf"(?:['\"]?{name}['\"]?)\s*[:=]\s*['\"]([^'\"]+)['\"]", text)
        return match.group(1) if match else None
    interval_match = re.search(r"(?:['\"]?interval_s['\"]?)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", text)
    return value("taskStartQuery"), value("taskStartToken"), float(interval_match.group(1)) if interval_match else None


def extract_antiflood_cookie(text: str) -> str | None:
    """Extract only the antiflood value from the JavaScript cookie assignment."""
    decoded = html.unescape(text)
    assignments = re.findall(r"document\.cookie\s*=\s*(['\"])(.*?)\1", decoded, re.IGNORECASE | re.DOTALL)
    for assignment in assignments:
        for part in assignment[1].split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name.strip().lower() == "antiflood" and value.strip():
                return value.strip()
    match = re.search(r"(?:^|[;\s])antiflood\s*=\s*([^;\s\"']+)", decoded, re.IGNORECASE)
    return match.group(1) if match else None


def _tcp_is_mainland(item: dict) -> bool:
    text = " ".join(str(item.get(key, "")) for key in ("location", "address", "provider", "name", "head"))
    return any(term in text for term in TcpPingChecker.MAINLAND_TERMS)


def aggregate_tcp_ping_results(items: list[dict], min_cn_probes: int = 3, blocked_success_rate: float = 0.2) -> CheckResult:
    mainland = [item for item in items if _tcp_is_mainland(item)]
    success = failure = unknown = 0
    for item in mainland:
        result = item.get("result")
        try:
            result = float(result)
        except (TypeError, ValueError):
            result = None
        if result is not None:
            if result == 1:
                failure += 1
            elif result == 0 or result > 1:
                success += 1
            else:
                unknown += 1
        else:
            unknown += 1
    total = len(mainland)
    reason = f"source=tcp.ping.pe, cn_success={success}, cn_failure={failure}, cn_unknown={unknown}, cn_total={total}"
    if total < min_cn_probes:
        return CheckResult(STATUS_UNKNOWN, reason + ", insufficient mainland probes")
    effective = success + failure
    rate = success / effective if effective else 0.0
    if not effective:
        return CheckResult(STATUS_UNKNOWN, reason + ", no effective probe results")
    return CheckResult(STATUS_BLOCKED if rate <= blocked_success_rate else STATUS_NORMAL, reason + f", success_rate={rate:.2%}")


class StatusStore:
    def __init__(self, database_path: str):
        self.database_path = database_path
        Path(database_path).parent.mkdir(parents=True, exist_ok=True) if Path(database_path).parent != Path(".") else None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS status_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_key TEXT NOT NULL,
                    name TEXT,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    resolved_ip TEXT,
                    status TEXT NOT NULL,
                    reason TEXT,
                    checked_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status_target_time ON status_checks(target_key, checked_at)")

    @staticmethod
    def target_key(target: TargetConfig) -> str:
        return f"{target.host}:{target.port}"

    def save(self, target: TargetConfig, resolved_ip: str | None, status: str, reason: str, checked_at: datetime | None = None) -> None:
        checked_at = checked_at or now_utc()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO status_checks(target_key, name, host, port, resolved_ip, status, reason, checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.target_key(target),
                    target.name,
                    target.host,
                    target.port,
                    resolved_ip,
                    status,
                    reason,
                    checked_at.isoformat(),
                ),
            )

    def latest(self, target: TargetConfig) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM status_checks WHERE target_key = ? ORDER BY checked_at DESC, id DESC LIMIT 1",
                (self.target_key(target),),
            ).fetchone()
        return dict(row) if row else None

    def recent(self, target: TargetConfig, minutes: int = 60) -> list[dict]:
        since = now_utc() - timedelta(minutes=minutes)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM status_checks WHERE target_key = ? AND checked_at >= ? ORDER BY checked_at ASC, id ASC",
                (self.target_key(target), since.isoformat()),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune_older_than(self, minutes: int = 120) -> None:
        cutoff = now_utc() - timedelta(minutes=minutes)
        with self._connect() as conn:
            conn.execute("DELETE FROM status_checks WHERE checked_at < ?", (cutoff.isoformat(),))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def is_ipv4_literal(host: str) -> bool:
    try:
        ipaddress.IPv4Address(host)
        return True
    except ipaddress.AddressValueError:
        return False


async def resolve_ipv4(host: str) -> str | None:
    if is_ipv4_literal(host):
        return host
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    if not infos:
        return None
    return infos[0][4][0]


def calculate_normal_rate(records: list[dict]) -> float:
    effective = [row for row in records if row.get("status") in {STATUS_NORMAL, STATUS_BLOCKED}]
    if not effective:
        return 0.0
    normal = sum(1 for row in effective if row.get("status") == STATUS_NORMAL)
    return round(normal / len(effective) * 100, 2)


def _parse_checked_at(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def aggregate_minute_history(records: list[dict], minutes: int = 60) -> list[dict]:
    """Return fixed UTC minute buckets, with blocked taking precedence."""
    current_minute = now_utc().replace(second=0, microsecond=0)
    buckets: dict[datetime, list[dict]] = {
        current_minute - timedelta(minutes=index): [] for index in range(minutes)
    }
    for record in records:
        try:
            minute = _parse_checked_at(record["checked_at"]).replace(second=0, microsecond=0)
        except (KeyError, TypeError, ValueError):
            continue
        if minute in buckets:
            buckets[minute].append(record)

    result = []
    for minute in sorted(buckets):
        entries = buckets[minute]
        statuses = {entry.get("status") for entry in entries}
        if STATUS_BLOCKED in statuses:
            status = STATUS_BLOCKED
        elif STATUS_NORMAL in statuses:
            status = STATUS_NORMAL
        else:
            status = STATUS_UNKNOWN
        result.append({"checked_at": minute.isoformat(), "status": status})
    return result


def build_transition_message(
    target: TargetConfig,
    resolved_ip: str | None,
    old_status: str,
    new_status: str,
    normal_rate: float,
    checked_at: datetime,
) -> str:
    return "\n".join(
        [
            "🚨 被墙" if new_status == STATUS_BLOCKED else "✅ 恢复",
            f"主机: {target.display_name} ({target.host})",
            f"解析目标IP: {resolved_ip or '未解析'}",
            f"端口: {target.port}",
            f"状态变化: {old_status} -> {new_status}",
            f"最近一小时正常率: {normal_rate:.2f}%",
            f"时间: {checked_at.astimezone(timezone.utc).isoformat()}",
        ]
    )


class Monitor:
    def __init__(self, config: AppConfig, checker: ConnectivityChecker | None = None, notifier: TelegramNotifier | None = None):
        self.config = config
        self.store = StatusStore(config.database_path)
        self.checker = checker or TcpPingChecker(config=config.tcp_ping)
        self.notifier = notifier or TelegramNotifier(config.telegram)
        self._stop_event = asyncio.Event()
        self._check_lock = asyncio.Lock()

    async def check_once(self, target: TargetConfig) -> dict:
        async with self._check_lock:
            return await self._check_once_unlocked(target)

    async def _check_once_unlocked(self, target: TargetConfig) -> dict:
        previous = self.store.latest(target)
        resolved_ip: str | None = None
        try:
            resolved_ip = await resolve_ipv4(target.host)
        except Exception as exc:  # noqa: BLE001 - DNS failures must not equal blocked.
            result = CheckResult(STATUS_UNKNOWN, f"resolve failure: {exc}")
        else:
            if not resolved_ip:
                result = CheckResult(STATUS_UNKNOWN, "resolve failure: no IPv4 address")
            else:
                result = await self.checker.check(target, resolved_ip)

        checked_at = now_utc()
        self.store.save(target, resolved_ip, result.status, result.reason, checked_at)
        self.store.prune_older_than(120)
        recent = self.store.recent(target, 61)
        normal_rate = calculate_normal_rate(aggregate_minute_history(recent, 60))

        previous_status = previous["status"] if previous else None
        if self._should_notify(previous_status, result.status):
            message = build_transition_message(target, resolved_ip, previous_status or STATUS_UNKNOWN, result.status, normal_rate, checked_at)
            try:
                await self.notifier.send(message)
            except Exception:  # noqa: BLE001 - notification failures must not stop monitoring.
                logging.exception("Failed to send Telegram notification")

        return {
            "target": {"name": target.name, "host": target.host, "port": target.port},
            "resolved_ip": resolved_ip,
            "status": result.status,
            "reason": result.reason,
            "checked_at": checked_at.isoformat(),
            "normal_rate_60m": normal_rate,
        }

    @staticmethod
    def _should_notify(old_status: str | None, new_status: str) -> bool:
        return (old_status == STATUS_NORMAL and new_status == STATUS_BLOCKED) or (
            old_status == STATUS_BLOCKED and new_status == STATUS_NORMAL
        )

    async def check_all_once(self) -> list[dict]:
        async with self._check_lock:
            return [await self._check_once_unlocked(target) for target in self.config.targets]

    async def run_forever(self) -> None:
        while not self._stop_event.is_set():
            await self.check_all_once()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.config.check_interval_seconds)
            except TimeoutError:
                continue

    def stop(self) -> None:
        self._stop_event.set()

    def api_status(self) -> dict:
        targets = []
        for target in self.config.targets:
            recent = self.store.recent(target, 61)
            latest = self.store.latest(target)
            history = aggregate_minute_history(recent, 60)
            targets.append(
                {
                    "name": target.name,
                    "host": target.host,
                    "port": target.port,
                    "latest": latest,
                    "normal_rate_60m": calculate_normal_rate(history),
                    "history": history,
                    "last_check_at": latest.get("checked_at") if latest else None,
                    "last_status": latest.get("status") if latest else STATUS_UNKNOWN,
                }
            )
        return {
            "refresh_seconds": self.config.check_interval_seconds,
            "targets": targets,
        }


def status_to_json(status: dict) -> str:
    return json.dumps(status, ensure_ascii=False)
