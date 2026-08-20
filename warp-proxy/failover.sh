#!/bin/sh

endpoint_port_at() {
    case "$1" in
        0) echo 2408 ;;
        1) echo 500 ;;
        2) echo 1701 ;;
        3) echo 4500 ;;
    esac
}

advance_endpoint() {
    if [ "$endpoint_index" -ge 3 ]; then
        echo "WARP endpoint candidates exhausted; rotating account." >&2
        return 1
    fi
    endpoint_index=$((endpoint_index + 1))
    endpoint_port=$(endpoint_port_at "$endpoint_index")
    sh "$profile_script" "$profile_path" "$ipv4_profile_path" "$endpoint_port"
    printf '%s\n' "$endpoint_index" > "$endpoint_index_path.tmp"
    mv "$endpoint_index_path.tmp" "$endpoint_index_path"
    printf '%s\n' "$endpoint_port" > "$endpoint_port_path.tmp"
    mv "$endpoint_port_path.tmp" "$endpoint_port_path"
    wireproxy --config "$config_path" --configtest
    echo "WARP endpoint switch: port $endpoint_port." >&2
}
