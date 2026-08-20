#!/bin/sh
set -eu

state_dir=$1
config_path=$2
account_path=$3
profile_path=$4
ipv4_profile_path=$5
profile_script=$6
endpoint_script=$7
failover_script=$8
endpoint_port_path="$state_dir/endpoint-port"
endpoint_index_path="$state_dir/endpoint-index"

readiness_url=${WARP_READINESS_URL:-http://127.0.0.1:9080/readyz}
grace_period=${WARP_READINESS_GRACE_PERIOD:-15}
readiness_interval=${WARP_READINESS_INTERVAL:-10}
failure_threshold=${WARP_READINESS_FAILURES:-6}
rotation_cooldown=${WARP_ROTATION_COOLDOWN:-300}
registration_backoff=${WARP_REGISTRATION_BACKOFF:-5}
registration_max_attempts=${WARP_REGISTRATION_MAX_ATTEMPTS:-5}
generation_path="$state_dir/rotation-generation"
last_rotation_path="$state_dir/rotation-last-at"
child_pid=

endpoint_state=$(sh "$endpoint_script" "$endpoint_port_path" "$endpoint_index_path")
endpoint_index=${endpoint_state%|*}
endpoint_port=${endpoint_state#*|}

. "$failover_script"

stop_child() {
    if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
        kill -TERM "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi
}
trap 'stop_child; exit 0' INT TERM HUP

register_account() {
    attempt=1
    delay=$registration_backoff
    while :; do
        if wgcf register --accept-tos >/dev/null 2>&1; then
            return 0
        fi
        if [ "$attempt" -ge "$registration_max_attempts" ]; then
            echo "WARP account registration failed after bounded retries." >&2
            return 1
        fi
        [ "$delay" -gt 60 ] && delay=60
        sleep "$delay"
        delay=$((delay * 2))
        attempt=$((attempt + 1))
    done
}

rotate_state() {
    now=$(date +%s)
    if [ -s "$last_rotation_path" ]; then
        last_rotation=$(cat "$last_rotation_path")
        if [ $((now - last_rotation)) -lt "$rotation_cooldown" ]; then
            echo "WARP rotation suppressed by cooldown." >&2
            return 1
        fi
    fi
    archive_dir="$state_dir/rotations/$now"
    mkdir -p "$archive_dir"
    for path in "$account_path" "$profile_path" "$ipv4_profile_path"; do
        if [ -e "$path" ]; then
            mv "$path" "$archive_dir/"
        fi
    done
    printf '%s\n' "$now" > "$last_rotation_path"
    cd "$state_dir"
    if ! register_account || ! wgcf generate >/dev/null 2>&1 || ! sh "$profile_script" "$profile_path" "$ipv4_profile_path" 2408; then
        rm -f "$account_path" "$profile_path" "$ipv4_profile_path"
        for archived_path in "$archive_dir"/*; do
            [ -e "$archived_path" ] && mv "$archived_path" "$state_dir/"
        done
        echo "WARP rotation failed; prior state restored." >&2
        return 1
    fi
    endpoint_index=0
    endpoint_port=2408
    printf '%s\n' "$endpoint_index" > "$endpoint_index_path"
    printf '%s\n' "$endpoint_port" > "$endpoint_port_path"
    generation=0
    [ -s "$generation_path" ] && generation=$(cat "$generation_path")
    printf '%s\n' $((generation + 1)) > "$generation_path"
    echo "WARP account rotation completed; generation $((generation + 1))." >&2
}

start_child() {
    wireproxy --config "$config_path" --info 0.0.0.0:9080 &
    child_pid=$!
    sleep "$grace_period"
}

while :; do
    start_child
    failures=0
    while kill -0 "$child_pid" 2>/dev/null; do
        if wget -q -T 5 -O /dev/null "$readiness_url"; then
            failures=0
        else
            failures=$((failures + 1))
            if [ "$failures" -ge "$failure_threshold" ]; then
                stop_child
                if advance_endpoint; then
                    break
                elif rotate_state; then
                    break
                else
                    break
                fi
            fi
        fi
        sleep "$readiness_interval"
    done
    wait "$child_pid" 2>/dev/null || true
    child_pid=
    if [ "$failures" -lt "$failure_threshold" ]; then
        echo "WireProxy exited unexpectedly; restarting." >&2
    fi
    sleep "$readiness_interval"
    continue
done
