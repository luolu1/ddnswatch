from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import monitor
from config import AppConfig, TargetConfig, TcpPingConfig, TelegramConfig, load_config
from monitor import (
    STATUS_BLOCKED,
    STATUS_NORMAL,
    STATUS_UNKNOWN,
    CheckResult,
    Monitor,
    TelegramBotService,
    TelegramNotifier,
    TcpPingChecker,
    aggregate_minute_history,
    aggregate_tcp_ping_results,
    build_transition_message,
    calculate_normal_rate,
    extract_antiflood_cookie,
    extract_tcp_ping_task,
)


def test_default_and_valid_check_intervals(tmp_path) -> None:
    assert AppConfig().check_interval_seconds == 25
    for interval in (20, 25, 30):
        path = tmp_path / f"config-{interval}.yaml"
        path.write_text(f"check_interval_seconds: {interval}\n", encoding="utf-8")
        assert load_config(path).check_interval_seconds == interval


@pytest.mark.parametrize("interval", (0, 19, 31))
def test_check_interval_is_validated(tmp_path, interval: int) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(f"check_interval_seconds: {interval}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_telegram_config_loads_multiple_chat_ids(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("telegram:\n  chat_ids: [42, '84']\n", encoding="utf-8")
    assert load_config(path).telegram.recipient_chat_ids == ("42", "84")


def test_telegram_config_keeps_legacy_chat_id(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("telegram:\n  chat_id: 42\n", encoding="utf-8")
    assert load_config(path).telegram.recipient_chat_ids == ("42",)


def test_tcp_task_and_cookie_extraction() -> None:
    assert extract_tcp_ping_task("taskStartQuery='ip:443'; taskStartToken=\"tok\"; interval_s=2") == ("ip:443", "tok", 2.0)
    assert extract_antiflood_cookie('document.cookie="foo=bar; antiflood=abc123; path=/"') == "abc123"


@pytest.mark.parametrize(
    ("values", "expected"),
    [([1, 1, 1], STATUS_BLOCKED), ([0, 0, 2], STATUS_NORMAL)],
)
def test_tcp_result_aggregation(values: list[int], expected: str) -> None:
    items = [{"node_id": str(index), "location": "China", "result": value} for index, value in enumerate(values)]
    assert aggregate_tcp_ping_results(items).status == expected


class FakeResponse:
    status_code = 200

    def __init__(self, payload=None, text=""):
        self._payload, self.text = payload, text

    def json(self):
        return self._payload


class FakeTcpClient:
    def __init__(self, **kwargs):
        self.calls = []
        self.cookies = type("Cookies", (), {"set": lambda this, name, value: setattr(this, name, value), "get": lambda this, name: getattr(this, name, None)})()
        self.polls = [
            {"state": {"outstandingNodeCount": 1, "outstandingNodes": [{"node_id": "a", "location": "China"}]}, "data": [{"node_id": "a", "result": 0}]},
            {"state": {"outstandingNodeCount": 0}, "data": [{"node_id": "b", "location": "中国", "result": 1}]},
        ]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        if "ajax_getPingResults" in url:
            return FakeResponse(self.polls.pop(0))
        if "ajax_stopTask" in url:
            return FakeResponse({})
        return FakeResponse(text="taskStartQuery='ip:443'; taskStartToken='tok'; interval_s=0")

    async def post(self, url, data=None, **kwargs):
        self.calls.append(("post", url, data))
        return FakeResponse({"ok": True, "data": {"stream_id": "stream"}})


@pytest.mark.asyncio
async def test_tcp_poll_merges_nodes_and_stops_task() -> None:
    client = FakeTcpClient()
    checker = TcpPingChecker(min_cn_probes=2, http_client_factory=lambda **kwargs: client, sleep=lambda _: _noop())
    result = await checker.check(TargetConfig("example.com", 443), "1.2.3.4")
    assert result.status == STATUS_NORMAL
    assert any("ajax_stopTask" in call[1] for call in client.calls if call[0] == "get")


class BrowserClient(FakeTcpClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.page_gets = 0

    async def get(self, url, **kwargs):
        if "ajax_getPingResults" not in url and "ajax_stopTask" not in url:
            self.page_gets += 1
            if "browsercheck=ok" not in url:
                return FakeResponse(text='document.cookie="antiflood=browser-token"')
            assert self.cookies.get("antiflood") == "browser-token"
            return FakeResponse(text="taskStartQuery='ip:443'; taskStartToken='dynamic'; interval_s=0")
        return await super().get(url, **kwargs)

    async def post(self, url, data=None, **kwargs):
        assert data == {"query": "ip:443", "start_token": "dynamic"}
        return await super().post(url, data, **kwargs)


@pytest.mark.asyncio
async def test_tcp_browser_cookie_and_dynamic_token() -> None:
    client = BrowserClient()
    checker = TcpPingChecker(min_cn_probes=2, http_client_factory=lambda **kwargs: client, sleep=lambda _: _noop())
    await checker.check(TargetConfig("example.com", 443), "1.2.3.4")
    assert client.page_gets == 2


async def _noop() -> None:
    return None


def _record(when: datetime, status: str) -> dict:
    return {"checked_at": when.isoformat(), "status": status}


def test_minute_history_has_60_buckets_and_blocked_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    current = datetime(2026, 8, 5, 12, 0, 45, tzinfo=timezone.utc)
    monkeypatch.setattr(monitor, "now_utc", lambda: current)
    records = [_record(current - timedelta(seconds=5), STATUS_NORMAL), _record(current - timedelta(seconds=10), STATUS_BLOCKED)]
    history = aggregate_minute_history(records)
    assert len(history) == 60
    assert history[-1]["status"] == STATUS_BLOCKED
    assert history[0]["status"] == STATUS_UNKNOWN
    assert history == sorted(history, key=lambda row: row["checked_at"])


def test_normal_rate_uses_minute_buckets() -> None:
    history = [{"status": STATUS_NORMAL}, {"status": STATUS_BLOCKED}, {"status": STATUS_UNKNOWN}]
    assert calculate_normal_rate(history) == 50.0


def test_transition_message_uses_checked_at() -> None:
    message = build_transition_message(TargetConfig("example.com", 443), "1.2.3.4", STATUS_NORMAL, STATUS_BLOCKED, 75.0, datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc))
    assert "时间: 2026-08-05T12:00:00+00:00" in message
    assert message.startswith("🚨 被墙")
    restored = build_transition_message(TargetConfig("example.com", 443), "1.2.3.4", STATUS_BLOCKED, STATUS_NORMAL, 75.0, datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc))
    assert restored.startswith("✅ 恢复")


class FakeChecker:
    def __init__(self, statuses: list[str]):
        self.statuses = statuses

    async def check(self, target, resolved_ip):
        return CheckResult(self.statuses.pop(0), "fake")


class FakeNotifier:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(message)


@pytest.mark.asyncio
async def test_status_change_notifies_immediately_with_real_check_time(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(monitor, "resolve_ipv4", lambda host: _resolved())
    times = [datetime(2026, 8, 5, 12, 0, 1, tzinfo=timezone.utc), datetime(2026, 8, 5, 12, 0, 30, tzinfo=timezone.utc)]
    monkeypatch.setattr(monitor, "now_utc", lambda: times.pop(0) if times else datetime(2026, 8, 5, 12, 0, 30, tzinfo=timezone.utc))
    target = TargetConfig("example.com", 443)
    notifier = FakeNotifier()
    service = Monitor(AppConfig(database_path=str(tmp_path / "status.sqlite3"), targets=[target], telegram=TelegramConfig()), FakeChecker([STATUS_NORMAL, STATUS_BLOCKED]), notifier)
    await service.check_once(target)
    await service.check_once(target)
    assert len(notifier.messages) == 1
    assert "时间: 2026-08-05T12:00:30+00:00" in notifier.messages[0]


async def _resolved() -> str:
    return "198.51.100.10"


class FakeTelegramResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeTelegramClient:
    def __init__(self, **kwargs):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json):
        self.calls.append((url, json))
        return FakeTelegramResponse({"ok": True, "result": []})


@pytest.mark.asyncio
async def test_telegram_notifier_sends_to_every_configured_chat() -> None:
    config = TelegramConfig(enabled=True, bot_token="test-token", chat_ids=("42", "84"))
    client = FakeTelegramClient()
    notifier = TelegramNotifier(config, http_client_factory=lambda **kwargs: client)
    await notifier.send("test message")
    assert [payload["chat_id"] for _, payload in client.calls] == ["42", "84"]


@pytest.mark.asyncio
async def test_telegram_notifier_rejects_missing_chat_ids() -> None:
    notifier = TelegramNotifier(TelegramConfig(enabled=True, bot_token="test-token"))
    with pytest.raises(ValueError, match="chat_id or chat_ids"):
        await notifier.send("test message")


@pytest.mark.asyncio
async def test_telegram_startup_and_menu_use_injected_client(tmp_path) -> None:
    config = TelegramConfig(enabled=True, bot_token="test-token", chat_id="42")
    client = FakeTelegramClient()
    service = TelegramBotService(config, http_client_factory=lambda **kwargs: client)
    monitored = Monitor(AppConfig(database_path=str(tmp_path / "status.sqlite3"), targets=[TargetConfig("example.com", 443)]))
    await service.startup(monitored)
    assert "🚀 DDNSWatch 已上线" in service.startup_text(monitored)
    assert any(url.endswith("/setMyCommands") and payload["commands"] == service.COMMANDS for url, payload in client.calls)
    sent = next(payload for url, payload in client.calls if url.endswith("/sendMessage"))
    assert sent["reply_markup"] == service.KEYBOARD


class FakeBotNotifier:
    def __init__(self):
        self.messages = []

    async def send(self, message, reply_markup=None):
        self.messages.append(message)


class SlowRefreshMonitor:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.config = AppConfig(check_interval_seconds=25, targets=[])
        self.calls = 0

    def api_status(self):
        return {"targets": [], "refresh_seconds": 25}

    async def check_all_once(self):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return [{"target": {"host": "example.com", "port": 443}, "status": STATUS_NORMAL}]


@pytest.mark.asyncio
async def test_telegram_buttons_commands_and_nonblocking_refresh() -> None:
    config = TelegramConfig(enabled=True, bot_token="test-token", chat_id="42")
    notifier = FakeBotNotifier()
    service = TelegramBotService(config, notifier=notifier)
    monitored = SlowRefreshMonitor()
    update = lambda text: {"message": {"chat": {"id": 42}, "text": text}}

    await service.handle_update(update("📊 查看全部状态"), monitored)
    await service.handle_update(update("ℹ️ 帮助"), monitored)
    assert "📊 DDNSWatch 全部状态" in notifier.messages[0]
    assert notifier.messages[1].startswith("ℹ️ 帮助")

    await service.handle_update(update("/status@botname extra text"), monitored)
    assert "📊 DDNSWatch 全部状态" in notifier.messages[2]

    await service.handle_update(update("🔄 立即检测"), monitored)
    assert notifier.messages[3] == "🔄 已开始检测，请稍候…"
    await monitored.started.wait()
    assert monitored.calls == 1
    await service.handle_update(update("/refresh"), monitored)
    assert notifier.messages[4] == "⏳ 检测正在进行中"
    monitored.release.set()
    await service._refresh_task
    assert notifier.messages[5].startswith("🔄 检测完成")
    await service.shutdown()


def test_telegram_authorization_and_status_text(tmp_path) -> None:
    target = TargetConfig("example.com", 443, "Example")
    monitored = Monitor(AppConfig(database_path=str(tmp_path / "status.sqlite3"), targets=[target]))
    monitored.store.save(target, "198.51.100.10", STATUS_NORMAL, "fake", datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc))
    service = TelegramBotService(TelegramConfig(enabled=True, bot_token="test-token", chat_id="42"))
    assert service.is_authorized(42)
    assert not service.is_authorized(43)
    text = service.status_text(monitored)
    assert "主机: Example (example.com)" in text
    assert "解析 IP: 198.51.100.10" in text
    assert "端口: 443" in text
    assert "当前状态: 正常" in text
    assert "最后检测时间: 2026-08-05T12:00:00+00:00" in text


def test_telegram_authorizes_every_configured_chat() -> None:
    service = TelegramBotService(
        TelegramConfig(enabled=True, bot_token="test-token", chat_ids=("42", "84"))
    )
    assert service.is_authorized(42)
    assert service.is_authorized(84)
    assert not service.is_authorized(21)
