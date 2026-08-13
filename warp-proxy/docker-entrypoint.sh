#!/bin/sh
set -eu

STATE_DIR="${WARP_STATE_DIR:-/var/lib/warp}"
ACCOUNT_PATH="$STATE_DIR/wgcf-account.toml"
PROFILE_PATH="$STATE_DIR/wgcf-profile.conf"
IPV4_PROFILE_PATH="$STATE_DIR/wgcf-profile-ipv4.conf"
WIREPROXY_CONFIG_PATH="$STATE_DIR/wireproxy.conf"

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

if ! awk '
function trim(value) {
    sub(/^[[:space:]]+/, "", value)
    sub(/[[:space:]]+$/, "", value)
    return value
}
/^[[:space:]]*Address[[:space:]]*=/ {
    split($0, assignment, "=")
    split(assignment[2], addresses, ",")
    for (address_index in addresses) {
        address = trim(addresses[address_index])
        if (address !~ /:/) {
            print "Address = " address
            ipv4_address_found = 1
            next
        }
    }
    exit 1
}
/^[[:space:]]*CheckAlive[[:space:]]*=/ { next }
/^[[:space:]]*CheckAliveInterval[[:space:]]*=/ { next }
/^[[:space:]]*AllowedIPs[[:space:]]*=/ {
    if (!allowed_ips_written) {
        print "AllowedIPs = 0.0.0.0/0"
        allowed_ips_written = 1
    }
    next
}
{ print }
END {
    if (!ipv4_address_found || !allowed_ips_written) {
        exit 1
    }
}
' "$PROFILE_PATH" > "$IPV4_PROFILE_PATH.tmp"; then
    rm -f "$IPV4_PROFILE_PATH.tmp"
    echo "IPv4 profile generation failed: the wgcf profile has no usable IPv4 address or route." >&2
    exit 1
fi
awk '
/^\[Peer\]$/ && !checks_written {
    print "CheckAlive = 1.1.1.1"
    print "CheckAliveInterval = 5"
    checks_written = 1
}
{ print }
' "$IPV4_PROFILE_PATH.tmp" > "$IPV4_PROFILE_PATH"
rm "$IPV4_PROFILE_PATH.tmp"

if grep -E '^[[:space:]]*(Address|AllowedIPs)[[:space:]]*=' "$IPV4_PROFILE_PATH" | grep -q ':'; then
    echo "IPv4 profile generation failed: IPv6 values remain after rewrite." >&2
    exit 1
fi

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
exec wireproxy --config "$WIREPROXY_CONFIG_PATH" --info 0.0.0.0:9080
