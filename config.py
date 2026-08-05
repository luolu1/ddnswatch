from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class TargetConfig:
    host: str
    port: int
    name: str | None = None

    @property
    def display_name(self) -> str:
        return self.name or self.host


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool = False
    bot_token: str | None = None
    chat_id: str | None = None
    poll_commands: bool = True
    startup_notification: bool = True


@dataclass(frozen=True)
class TcpPingConfig:
    enabled: bool = True
    base_url: str = "https://tcp.ping.pe"
    min_cn_probes: int = 3
    blocked_success_rate: float = 0.2
    timeout_seconds: float = 20.0
    max_polls: int = 20
    poll_interval_seconds: float = 3.0


@dataclass(frozen=True)
class AppConfig:
    check_interval_seconds: int = 25
    targets: list[TargetConfig] = field(default_factory=list)
    tcp_ping: TcpPingConfig = field(default_factory=TcpPingConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    database_path: str = "ddnswatch.sqlite3"


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        return AppConfig()

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw = _require_mapping(raw, "config")

    targets: list[TargetConfig] = []
    for index, item in enumerate(raw.get("targets", []), start=1):
        item = _require_mapping(item, f"targets[{index}]")
        host = item.get("host") or item.get("domain")
        if not host:
            raise ValueError(f"targets[{index}] requires host or domain")
        if "port" not in item:
            raise ValueError(f"targets[{index}] requires port")
        targets.append(
            TargetConfig(
                host=str(host),
                port=int(item["port"]),
                name=str(item["name"]) if item.get("name") is not None else None,
            )
        )

    tg_raw = _require_mapping(raw.get("telegram", {}), "telegram")
    telegram = TelegramConfig(
        enabled=bool(tg_raw.get("enabled", False)),
        bot_token=str(tg_raw["bot_token"]) if tg_raw.get("bot_token") else None,
        chat_id=str(tg_raw["chat_id"]) if tg_raw.get("chat_id") else None,
        poll_commands=bool(tg_raw.get("poll_commands", True)),
        startup_notification=bool(tg_raw.get("startup_notification", True)),
    )

    tcp_raw = _require_mapping(raw.get("tcp_ping", {}), "tcp_ping")
    tcp_ping = TcpPingConfig(
        enabled=bool(tcp_raw.get("enabled", TcpPingConfig.enabled)),
        base_url=str(tcp_raw.get("base_url", TcpPingConfig.base_url)),
        min_cn_probes=int(tcp_raw.get("min_cn_probes", TcpPingConfig.min_cn_probes)),
        blocked_success_rate=float(tcp_raw.get("blocked_success_rate", TcpPingConfig.blocked_success_rate)),
        timeout_seconds=float(tcp_raw.get("timeout_seconds", TcpPingConfig.timeout_seconds)),
        max_polls=int(tcp_raw.get("max_polls", TcpPingConfig.max_polls)),
        poll_interval_seconds=float(tcp_raw.get("poll_interval_seconds", TcpPingConfig.poll_interval_seconds)),
    )
    if tcp_ping.min_cn_probes < 1:
        raise ValueError("tcp_ping.min_cn_probes must be at least 1")
    if not 0 <= tcp_ping.blocked_success_rate <= 1:
        raise ValueError("tcp_ping.blocked_success_rate must be between 0 and 1")
    if tcp_ping.timeout_seconds <= 0 or tcp_ping.max_polls < 1 or tcp_ping.poll_interval_seconds < 0:
        raise ValueError("tcp_ping timeout/max_polls/interval settings are invalid")

    interval = int(raw.get("check_interval_seconds", 25))
    if interval < 1:
        raise ValueError("check_interval_seconds must be at least 1")
    if not 20 <= interval <= 30:
        raise ValueError("check_interval_seconds must be between 20 and 30")

    return AppConfig(
        database_path=str(raw.get("database_path", "ddnswatch.sqlite3")),
        check_interval_seconds=interval,
        targets=targets,
        tcp_ping=tcp_ping,
        telegram=telegram,
    )
