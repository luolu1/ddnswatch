#!/bin/sh
set -eu

STATE_DIR=${WARP_STATE_DIR:-/var/lib/warp}
ACCOUNT_PATH="$STATE_DIR/wgcf-account.toml"
PROFILE_PATH="$STATE_DIR/wgcf-profile.conf"
IPV4_PROFILE_PATH="$STATE_DIR/wgcf-profile-ipv4.conf"
WIREPROXY_CONFIG_PATH="$STATE_DIR/wireproxy.conf"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

mkdir -p "$STATE_DIR"
cd "$STATE_DIR"

if [ ! -s "$ACCOUNT_PATH" ]; then
    wgcf register --accept-tos || {
        status=$?
        echo "WARP account registration failed; check upstream availability and retry. No credentials were saved." >&2
        exit "$status"
    }
fi

if [ ! -s "$PROFILE_PATH" ]; then
    wgcf generate || {
        status=$?
        echo "WARP profile generation failed; the persisted account was kept for retry." >&2
        exit "$status"
    }
fi

sh "$SCRIPT_DIR/profile.sh" "$PROFILE_PATH" "$IPV4_PROFILE_PATH"
cat > "$WIREPROXY_CONFIG_PATH.tmp" <<EOF
WGConfig = $IPV4_PROFILE_PATH

[Socks5]
BindAddress = 0.0.0.0:25344

[http]
BindAddress = 0.0.0.0:25345

[Resolve]
ResolveStrategy = ipv4
EOF
mv "$WIREPROXY_CONFIG_PATH.tmp" "$WIREPROXY_CONFIG_PATH"
wireproxy --config "$WIREPROXY_CONFIG_PATH" --configtest
exec sh "$SCRIPT_DIR/supervisor.sh" "$STATE_DIR" "$WIREPROXY_CONFIG_PATH" "$ACCOUNT_PATH" "$PROFILE_PATH" "$IPV4_PROFILE_PATH" "$SCRIPT_DIR/profile.sh"
