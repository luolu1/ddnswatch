#!/bin/sh
set -eu

endpoint_port_path=$1
endpoint_index_path=$2
endpoint_ports=${WARP_ENDPOINT_PORTS:-2408,500,1701,4500}

case "$endpoint_ports" in
    2408,500,1701,4500) : ;;
    *) echo "WARP endpoint ports must be exactly 2408,500,1701,4500." >&2; exit 1 ;;
esac

endpoint_port_at() {
    case "$1" in
        0) echo 2408 ;;
        1) echo 500 ;;
        2) echo 1701 ;;
        3) echo 4500 ;;
    esac
}

endpoint_index=0
if [ -s "$endpoint_index_path" ]; then endpoint_index=$(cat "$endpoint_index_path"); fi
case "$endpoint_index" in 0|1|2|3) : ;; *) endpoint_index=0 ;; esac
if [ ! -s "$endpoint_index_path" ] && [ -s "$endpoint_port_path" ]; then
    case "$(cat "$endpoint_port_path")" in
        2408) endpoint_index=0 ;;
        500) endpoint_index=1 ;;
        1701) endpoint_index=2 ;;
        4500) endpoint_index=3 ;;
    esac
fi
endpoint_port=$(endpoint_port_at "$endpoint_index")
printf '%s|%s\n' "$endpoint_index" "$endpoint_port"
