from __future__ import annotations

import os
import socketserver
import subprocess
import threading
from pathlib import Path

import httpx
import pytest
import yaml

from monitor import create_http_client


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "warp-proxy" / "docker-entrypoint.sh"


def load_compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def run_entrypoint(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    bin_dir.mkdir(exist_ok=True)
    state_dir.mkdir(exist_ok=True)
    (bin_dir / "wgcf").write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$WARP_STATE_DIR/wgcf.calls"
case "$1" in
  register) printf 'account' > "$WARP_STATE_DIR/wgcf-account.toml" ;;
  generate) cat > "$WARP_STATE_DIR/wgcf-profile.conf" <<'EOF'
[Interface]
PrivateKey = test-private-key
Address = 172.16.0.2/32, 2606:4700:110:1234::2/128
DNS = 1.1.1.1
CheckAlive = 9.9.9.9
CheckAliveInterval = 99

[Peer]
PublicKey = test-public-key
AllowedIPs = 0.0.0.0/0, ::/0
AllowedIPs = ::/0
Endpoint = engage.cloudflareclient.com:2408
EOF
esac
""",
        encoding="utf-8",
    )
    (bin_dir / "wireproxy").write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$WARP_STATE_DIR/wireproxy.calls"
""",
        encoding="utf-8",
    )
    os.chmod(bin_dir / "wgcf", 0o755)
    os.chmod(bin_dir / "wireproxy", 0o755)
    return subprocess.run(
        ["sh", str(ENTRYPOINT)],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WARP_STATE_DIR": str(state_dir),
        },
    )


def test_compose_uses_local_healthy_gateway_and_persistent_state() -> None:
    compose = load_compose()
    services = compose["services"]
    warp_proxy = services["warp-proxy"]
    ddnswatch = services["ddnswatch"]

    assert warp_proxy["build"] == {"context": "./warp-proxy"}
    assert warp_proxy["volumes"] == ["warp-state:/var/lib/warp"]
    assert "warp-state" in compose["volumes"]
    assert warp_proxy["healthcheck"]["test"] == [
        "CMD",
        "wget",
        "-q",
        "-T",
        "5",
        "-O",
        "/dev/null",
        "http://127.0.0.1:9080/readyz",
    ]
    assert ddnswatch["depends_on"] == {"warp-proxy": {"condition": "service_healthy"}}
    assert ddnswatch["ports"] == ["${DDNSWATCH_PORT:-8000}:8000"]
    assert "./data:/app/data" in ddnswatch["volumes"]
    assert "warp/warp.conf" not in (ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def test_compose_routes_all_httpx_traffic_through_http_proxy() -> None:
    environment = load_compose()["services"]["ddnswatch"]["environment"]

    assert environment["HTTP_PROXY"] == "http://warp-proxy:25345"
    assert environment["HTTPS_PROXY"] == "http://warp-proxy:25345"
    assert environment["ALL_PROXY"] == "http://warp-proxy:25345"
    assert environment["NO_PROXY"] == "localhost,127.0.0.1,::1"
    assert environment["http_proxy"] == environment["HTTP_PROXY"]
    assert environment["https_proxy"] == environment["HTTPS_PROXY"]
    assert environment["all_proxy"] == environment["ALL_PROXY"]
    assert environment["no_proxy"] == environment["NO_PROXY"]


def test_first_start_registers_and_generates_ipv4_wireproxy_config(tmp_path: Path) -> None:
    result = run_entrypoint(tmp_path)
    state_dir = tmp_path / "state"
    config = (state_dir / "wireproxy.conf").read_text(encoding="utf-8")
    profile = (state_dir / "wgcf-profile-ipv4.conf").read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert (state_dir / "wgcf.calls").read_text(encoding="utf-8").splitlines() == [
        "register --accept-tos",
        "generate",
    ]
    assert "Address = 172.16.0.2/32" in profile
    assert "CheckAlive = 1.1.1.1" in profile
    assert "CheckAliveInterval = 5" in profile
    assert profile.count("CheckAlive =") == 1
    assert profile.count("CheckAliveInterval =") == 1
    assert "AllowedIPs = 0.0.0.0/0" in profile
    assert "::/0" not in profile
    assert "2606:" not in profile
    assert "[Socks5]\nBindAddress = 0.0.0.0:25344" in config
    assert "[http]\nBindAddress = 0.0.0.0:25345" in config
    assert "[Resolve]\nResolveStrategy = ipv4" in config
    assert (state_dir / "wireproxy.calls").read_text(encoding="utf-8").splitlines() == [
        f"--config {state_dir / 'wireproxy.conf'} --configtest",
        f"--config {state_dir / 'wireproxy.conf'} --info 0.0.0.0:9080",
    ]


def test_later_start_reuses_persisted_credentials(tmp_path: Path) -> None:
    first = run_entrypoint(tmp_path)
    second = run_entrypoint(tmp_path)
    calls = (tmp_path / "state" / "wgcf.calls").read_text(encoding="utf-8").splitlines()

    assert first.returncode == 0
    assert second.returncode == 0
    assert calls == ["register --accept-tos", "generate"]


def test_registration_failure_is_clear_and_leaves_no_account(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    bin_dir.mkdir()
    state_dir.mkdir()
    (bin_dir / "wgcf").write_text("#!/bin/sh\nexit 17\n", encoding="utf-8")
    os.chmod(bin_dir / "wgcf", 0o755)

    result = subprocess.run(
        ["sh", str(ENTRYPOINT)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "WARP_STATE_DIR": str(state_dir)},
    )

    assert result.returncode == 17
    assert "WARP account registration failed" in result.stderr
    assert not (state_dir / "wgcf-account.toml").exists()


def test_ipv6_only_profile_fails_closed(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    bin_dir.mkdir()
    state_dir.mkdir()
    (state_dir / "wgcf-account.toml").write_text("account", encoding="utf-8")
    (state_dir / "wgcf-profile.conf").write_text(
        "[Interface]\nAddress = 2606:4700::2/128\n[Peer]\nAllowedIPs = ::/0\n",
        encoding="utf-8",
    )
    (bin_dir / "wireproxy").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(bin_dir / "wireproxy", 0o755)

    result = subprocess.run(
        ["sh", str(ENTRYPOINT)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "WARP_STATE_DIR": str(state_dir)},
    )

    assert result.returncode != 0
    assert "IPv4 profile generation failed" in result.stderr


def test_runtime_image_contains_healthcheck_client() -> None:
    dockerfile = (ROOT / "warp-proxy" / "Dockerfile").read_text(encoding="utf-8")

    assert "apk add --no-cache ca-certificates wget" in dockerfile


@pytest.mark.asyncio
async def test_http_client_uses_https_proxy_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[str] = []

    class ConnectProxy(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            requests.append(self.rfile.readline().decode("ascii").strip())
            self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")

    with socketserver.TCPServer(("127.0.0.1", 0), ConnectProxy) as proxy:
        thread = threading.Thread(target=proxy.serve_forever)
        thread.start()
        monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{proxy.server_address[1]}")
        monkeypatch.setenv("NO_PROXY", "")
        try:
            async with create_http_client(timeout=1) as client:
                with pytest.raises(httpx.ProxyError):
                    await client.get("https://example.invalid/")
        finally:
            proxy.shutdown()
            thread.join()

    assert requests == ["CONNECT example.invalid:443 HTTP/1.1"]


def test_dockerfile_builds_immutable_module_versions_without_runtime_downloads() -> None:
    dockerfile = (ROOT / "warp-proxy" / "Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile.split("FROM alpine:", maxsplit=1)[1]

    assert "--branch v2.2.29 --depth 1 https://github.com/ViRb3/wgcf.git" in dockerfile
    assert "7f74511fa8cd1187df4b1d5351ebda3dcab82825" in dockerfile
    assert "--branch v1.1.2 --depth 1 https://github.com/windtf/wireproxy.git" in dockerfile
    assert "3792cd42b2ffb9653fb4beb3fe9c66d1512f9ce0" in dockerfile
    assert "TARGETARCH" in dockerfile
    assert "ca-certificates wget" in dockerfile
    assert "curl" not in runtime
    assert "wget http" not in runtime


def test_no_warp_credentials_or_manual_config_are_committed() -> None:
    tracked_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts
    )

    assert "test-private-key" not in tracked_text.replace(
        (ROOT / "tests" / "test_warp_deployment.py").read_text(encoding="utf-8"), ""
    )
    assert not (ROOT / "warp" / "warp.conf").exists()
    assert not (ROOT / "warp" / "warp.conf.example").exists()
    assert "WARP_PROXY_CONFIG" not in (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
