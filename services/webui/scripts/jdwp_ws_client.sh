#!/bin/bash
# 用法: ./jdwp_ws_client.sh <jdwp-ip> [jdwp-port] [tcp-port]
# 示例: ./jdwp_ws_client.sh 10.147.182.149

JDWP_IP="$1"
JDWP_PORT="${2:-2345}"
TCP_PORT="${3:-8010}"

if [ -z "$JDWP_IP" ]; then
  echo "用法: $0 <jdwp-ip> [jdwp-port] [tcp-port]"
  echo "示例: $0 10.147.182.149"
  exit 1
fi

BIN="/Users/zhouchentao1/Downloads/jdwp_ws_client_mac_arm"

chmod +x "$BIN"
"$BIN" "-tcp-port=${TCP_PORT}" "-jdwp-addr=${JDWP_IP}:${JDWP_PORT}"
