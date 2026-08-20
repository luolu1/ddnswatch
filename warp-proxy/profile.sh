#!/bin/sh
set -eu

profile_path=$1
output_path=$2
temporary_path="$output_path.tmp"
rewritten_path="$output_path.rewritten.tmp"
endpoint_port=${3:-2408}

case "$endpoint_port" in
    2408|500|1701|4500) : ;;
    *) echo "IPv4 profile generation failed: unsupported endpoint port." >&2; exit 1 ;;
esac

awk -v endpoint_port="$endpoint_port" '
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
/^[[:space:]]*DNS[[:space:]]*=/ {
    if (!dns_written) {
        print "DNS = 1.1.1.1"
        dns_written = 1
    }
    next
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
/^[[:space:]]*Endpoint[[:space:]]*=/ {
    print "Endpoint = engage.cloudflareclient.com:" endpoint_port
    endpoint_written = 1
    next
}
{ print }
END {
    if (!ipv4_address_found || !allowed_ips_written || !dns_written || !endpoint_written) {
        exit 1
    }
}
' "$profile_path" > "$temporary_path" || {
    rm -f "$temporary_path"
    echo "IPv4 profile generation failed: the wgcf profile has no usable IPv4 address or route." >&2
    exit 1
}

awk '
/^\[Peer\]$/ && !checks_written {
    print "CheckAlive = 1.1.1.1"
    print "CheckAliveInterval = 5"
    checks_written = 1
}
{ print }
' "$temporary_path" > "$rewritten_path"
mv "$rewritten_path" "$output_path"
rm -f "$temporary_path"

if grep -E '^[[:space:]]*(Address|AllowedIPs|DNS)[[:space:]]*=' "$output_path" | grep -q ':'; then
    echo "IPv4 profile generation failed: IPv6 values remain after rewrite." >&2
    exit 1
fi
