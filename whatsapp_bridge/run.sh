#!/bin/sh
# Starts the WhatsApp bridge and the read-only unanswered-chats API side by side.
# If either dies, the add-on exits so the Supervisor restarts it cleanly.
set -eu

CONFIG=/data/options.json

WA_API_TOKEN="$(jq -r '.api_token // ""'      "$CONFIG")"
WA_GRACE_HOURS="$(jq -r '.grace_hours // 4'   "$CONFIG")"
WA_LOOKBACK_DAYS="$(jq -r '.lookback_days // 14' "$CONFIG")"
WA_INCLUDE_GROUPS="$(jq -r '.include_groups // false' "$CONFIG")"
WA_DB=/data/store/messages.db
export WA_API_TOKEN WA_GRACE_HOURS WA_LOOKBACK_DAYS WA_INCLUDE_GROUPS WA_DB

mkdir -p /data/store
cd /data

bridge_pid=""
api_pid=""

shutdown() {
    [ -n "$bridge_pid" ] && kill -TERM "$bridge_pid" 2>/dev/null || true
    [ -n "$api_pid" ]    && kill -TERM "$api_pid"    2>/dev/null || true
}
trap 'shutdown' TERM INT

echo "[addon] starting whatsapp-bridge (REST API on :8080)"
whatsapp-bridge &
bridge_pid=$!

echo "[addon] starting unanswered API on :8081"
python3 -u /opt/unanswered/unanswered_api.py &
api_pid=$!

# Supervise both. busybox ash has no reliable `wait -n`, so poll.
while kill -0 "$bridge_pid" 2>/dev/null && kill -0 "$api_pid" 2>/dev/null; do
    sleep 5
done

echo "[addon] a child process exited - stopping the other and restarting"
shutdown
wait || true
exit 1
