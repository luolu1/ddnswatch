#!/bin/sh
set -eu

CONFIG_PATH="${DDNSWATCH_CONFIG:-/app/data/config.yaml}"
CONFIG_DIR=$(dirname "$CONFIG_PATH")

mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_PATH" ]; then
    cp /app/config.example.yaml "$CONFIG_PATH"
    echo "Created initial configuration at $CONFIG_PATH; edit it before enabling production monitoring."
fi

exec "$@"
