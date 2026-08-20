from __future__ import annotations

import os
import socketserver
import subprocess
import threading
import time
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
DNS = 1.1.1.1, 1.0.0.1, 2606:4700:4700::1001
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
case "$1" in
  --config) case "$*" in *--configtest) exit 0 ;; esac ;;
esac
kill -TERM "$PPID"
""",
        encoding="utf-8",
    )
    (bin_dir / "wget").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for path in (bin_dir / "wgcf", bin_dir / "wireproxy", bin_dir / "wget"):
        os.chmod(path, 0o755)
    return subprocess.run(
        ["sh", str(ENTRYPOINT)],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WARP_STATE_DIR": str(state_dir),
            "WARP_READINESS_GRACE_PERIOD": "0",
            "WARP_READINESS_INTERVAL": "0",
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
    assert "DNS = 1.1.1.1" in profile
    assert profile.count("DNS =") == 1
    assert "[Socks5]\nBindAddress = 0.0.0.0:25344" in config
    assert "[http]\nBindAddress = 0.0.0.0:25345" in config
    assert "[Resolve]\nResolveStrategy = ipv4" in config
    assert (state_dir / "wireproxy.calls").read_text(encoding="utf-8").splitlines() == [
        f"--config {state_dir / 'wireproxy.conf'} --configtest",
        f"--config {state_dir / 'wireproxy.conf'} --info 0.0.0.0:9080",
    ]


def test_persistent_unhealthy_readiness_switches_endpoint_before_rotation(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    bin_dir.mkdir()
    state_dir.mkdir()
    (bin_dir / "wgcf").write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$WARP_STATE_DIR/wgcf.calls"
case "$1" in
  register) printf 'account-%s' "$(wc -l < "$WARP_STATE_DIR/wgcf.calls")" > "$WARP_STATE_DIR/wgcf-account.toml" ;;
  generate) printf '[Interface]\\nAddress = 172.16.0.2/32\\nDNS = 1.1.1.1\\n[Peer]\\nAllowedIPs = 0.0.0.0/0\\nEndpoint = engage.cloudflareclient.com:2408\\n' > "$WARP_STATE_DIR/wgcf-profile.conf" ;;
esac
""",
        encoding="utf-8",
    )
    (bin_dir / "wireproxy").write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$WARP_STATE_DIR/wireproxy.calls"
case "$1" in
  --config) case "$*" in *--configtest) exit 0 ;; esac ;;
esac
start_count=$(grep -c -- '--info' "$WARP_STATE_DIR/wireproxy.calls")
if [ "$start_count" -gt 1 ]; then kill -TERM "$PPID"; exit 0; fi
trap 'exit 0' TERM INT
while :; do sleep 1; done
""",
        encoding="utf-8",
    )
    (bin_dir / "wget").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    for path in (bin_dir / "wgcf", bin_dir / "wireproxy", bin_dir / "wget"):
        os.chmod(path, 0o755)

    result = subprocess.run(
        ["sh", str(ENTRYPOINT)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WARP_STATE_DIR": str(state_dir),
            "WARP_READINESS_GRACE_PERIOD": "0",
            "WARP_READINESS_INTERVAL": "0",
            "WARP_READINESS_FAILURES": "2",
            "WARP_ROTATION_COOLDOWN": "0",
            "WARP_REGISTRATION_BACKOFF": "0",
            "WARP_REGISTRATION_MAX_ATTEMPTS": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    assert (state_dir / "wgcf.calls").read_text(encoding="utf-8").splitlines() == [
        "register --accept-tos",
        "generate",
    ]
    assert (state_dir / "endpoint-port").read_text(encoding="utf-8").strip() == "500"


def _run_until_marker(process: subprocess.Popen[str], marker: Path) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.01)
    process.terminate()
    process.wait(timeout=5)
    assert marker.exists()


def test_endpoint_failover_preserves_account_and_persists_port(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    marker = state_dir / "port-500-ready"
    bin_dir.mkdir()
    state_dir.mkdir()
    (bin_dir / "wgcf").write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$WARP_STATE_DIR/wgcf.calls"
case "$1" in
  register) printf 'account-secret' > "$WARP_STATE_DIR/wgcf-account.toml" ;;
  generate) printf '[Interface]\\nPrivateKey = private-key\\nAddress = 172.16.0.2/32\\nDNS = 1.1.1.1\\nMTU = 1280\\n[Peer]\\nPublicKey = peer-key\\nAllowedIPs = 0.0.0.0/0\\nCheckAlive = 1.1.1.1\\nCheckAliveInterval = 5\\nEndpoint = engage.cloudflareclient.com:2408\\n' > "$WARP_STATE_DIR/wgcf-profile.conf" ;;
esac
""",
        encoding="utf-8",
    )
    (bin_dir / "wireproxy").write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$WARP_STATE_DIR/wireproxy.calls"
case "$*" in *--configtest) exit 0 ;; esac
trap 'exit 0' TERM INT
while :; do sleep 1; done
""",
        encoding="utf-8",
    )
    (bin_dir / "wget").write_text(
        """#!/bin/sh
set -eu
if grep -q 'Endpoint = engage.cloudflareclient.com:500' "$WARP_STATE_DIR/wgcf-profile-ipv4.conf"; then
  : > "$WARP_STATE_DIR/port-500-ready"
  exit 0
fi
exit 1
""",
        encoding="utf-8",
    )
    for path in (bin_dir / "wgcf", bin_dir / "wireproxy", bin_dir / "wget"):
        os.chmod(path, 0o755)
    process = subprocess.Popen(
        ["sh", str(ENTRYPOINT)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WARP_STATE_DIR": str(state_dir),
            "WARP_READINESS_GRACE_PERIOD": "0",
            "WARP_READINESS_INTERVAL": "0",
            "WARP_READINESS_FAILURES": "2",
            "WARP_ROTATION_COOLDOWN": "300",
        },
    )

    _run_until_marker(process, marker)

    assert (state_dir / "wgcf-account.toml").read_text(encoding="utf-8") == "account-secret"
    assert (state_dir / "wgcf-profile-ipv4.conf").read_text(encoding="utf-8").count(
        "Endpoint = engage.cloudflareclient.com:500"
    ) == 1
    assert (state_dir / "wgcf.calls").read_text(encoding="utf-8").splitlines() == [
        "register --accept-tos",
        "generate",
    ]
    assert (state_dir / "endpoint-port").read_text(encoding="utf-8").strip() == "500"


def test_all_endpoint_failures_rotate_only_after_exhaustion(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    marker = state_dir / "rotated-ready"
    bin_dir.mkdir()
    state_dir.mkdir()
    (bin_dir / "wgcf").write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$WARP_STATE_DIR/wgcf.calls"
case "$1" in
  register) printf 'account-%s' "$(grep -c 'register' "$WARP_STATE_DIR/wgcf.calls")" > "$WARP_STATE_DIR/wgcf-account.toml" ;;
  generate) printf '[Interface]\\nPrivateKey = fresh-private\\nAddress = 172.16.0.2/32\\nDNS = 1.1.1.1\\n[Peer]\\nPublicKey = fresh-peer\\nAllowedIPs = 0.0.0.0/0\\nEndpoint = engage.cloudflareclient.com:2408\\n' > "$WARP_STATE_DIR/wgcf-profile.conf" ;;
esac
""",
        encoding="utf-8",
    )
    (bin_dir / "wireproxy").write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$WARP_STATE_DIR/wireproxy.calls"
case "$*" in *--configtest) exit 0 ;; esac
trap 'exit 0' TERM INT
while :; do sleep 1; done
""",
        encoding="utf-8",
    )
    (bin_dir / "wget").write_text(
        """#!/bin/sh
set -eu
if grep -q 'account-2' "$WARP_STATE_DIR/wgcf-account.toml"; then
  : > "$WARP_STATE_DIR/rotated-ready"
  exit 0
fi
exit 1
""",
        encoding="utf-8",
    )
    for path in (bin_dir / "wgcf", bin_dir / "wireproxy", bin_dir / "wget"):
        os.chmod(path, 0o755)
    process = subprocess.Popen(
        ["sh", str(ENTRYPOINT)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WARP_STATE_DIR": str(state_dir),
            "WARP_READINESS_GRACE_PERIOD": "0",
            "WARP_READINESS_INTERVAL": "0",
            "WARP_READINESS_FAILURES": "1",
            "WARP_ROTATION_COOLDOWN": "0",
            "WARP_REGISTRATION_BACKOFF": "0",
            "WARP_REGISTRATION_MAX_ATTEMPTS": "1",
        },
    )

    _run_until_marker(process, marker)

    calls = (state_dir / "wgcf.calls").read_text(encoding="utf-8").splitlines()
    assert calls == ["register --accept-tos", "generate", "register --accept-tos", "generate"]
    assert (state_dir / "endpoint-port").read_text(encoding="utf-8").strip() == "2408"


def test_supervisor_configuration_has_safe_defaults_and_real_readiness() -> None:
    compose = load_compose()["services"]["warp-proxy"]
    environment = compose["environment"]
    supervisor = (ROOT / "warp-proxy" / "supervisor.sh").read_text(encoding="utf-8")

    assert environment["WARP_READINESS_GRACE_PERIOD"] == "${WARP_READINESS_GRACE_PERIOD:-15}"
    assert environment["WARP_READINESS_INTERVAL"] == "${WARP_READINESS_INTERVAL:-10}"
    assert environment["WARP_READINESS_FAILURES"] == "${WARP_READINESS_FAILURES:-6}"
    assert environment["WARP_ROTATION_COOLDOWN"] == "${WARP_ROTATION_COOLDOWN:-300}"
    assert environment["WARP_ENDPOINT_PORTS"] == "${WARP_ENDPOINT_PORTS:-2408,500,1701,4500}"
    assert "http://127.0.0.1:9080/readyz" in supervisor
    assert "failures=0" in supervisor


def test_endpoint_candidates_are_exact_and_operator_events_are_secret_safe() -> None:
    supervisor = (ROOT / "warp-proxy" / "supervisor.sh").read_text(encoding="utf-8")
    profile = (ROOT / "warp-proxy" / "profile.sh").read_text(encoding="utf-8")

    assert "2408,500,1701,4500" in (ROOT / "warp-proxy" / "endpoint.sh").read_text(encoding="utf-8")
    assert "endpoint candidates exhausted" in (ROOT / "warp-proxy" / "failover.sh").read_text(encoding="utf-8")
    assert "endpoint switch" in (ROOT / "warp-proxy" / "failover.sh").read_text(encoding="utf-8")
    assert "account rotation" in supervisor
    assert "wgcf register --accept-tos >/dev/null 2>&1" in supervisor
    assert "Endpoint = engage.cloudflareclient.com:" in profile


def test_rotation_supervisor_bounds_registration_and_never_logs_command_output() -> None:
    supervisor = (ROOT / "warp-proxy" / "supervisor.sh").read_text(encoding="utf-8")

    assert "WARP_REGISTRATION_MAX_ATTEMPTS:-5" in supervisor
    assert "delay=$((delay * 2))" in supervisor
    assert "[ \"$delay\" -gt 60 ] && delay=60" in supervisor
    assert "wgcf register --accept-tos >/dev/null 2>&1" in supervisor
    assert "wgcf generate >/dev/null 2>&1" in supervisor
    assert "rotation-generation" in supervisor


def test_rotation_supervisor_preserves_reuse_cooldown_and_signal_contracts() -> None:
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
    supervisor = (ROOT / "warp-proxy" / "supervisor.sh").read_text(encoding="utf-8")

    assert 'if [ ! -s "$ACCOUNT_PATH" ]' in entrypoint
    assert 'if [ ! -s "$PROFILE_PATH" ]' in entrypoint
    assert "WARP_ROTATION_COOLDOWN:-300" in supervisor
    assert 'trap \'stop_child; exit 0\' INT TERM HUP' in supervisor
    assert 'kill -TERM "$child_pid"' in supervisor


def test_supervisor_restarts_when_wireproxy_exits_unexpectedly() -> None:
    supervisor = (ROOT / "warp-proxy" / "supervisor.sh").read_text(encoding="utf-8")

    assert 'echo "WireProxy exited unexpectedly; restarting."' in supervisor
    assert "child_pid=" in supervisor
    assert "continue" in supervisor


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
