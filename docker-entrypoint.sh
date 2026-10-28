#!/bin/sh
# Cloud disks (Render, Railway, Docker volumes) are often mounted owned by root.
# Fix ownership of the data/log dirs, then drop to the unprivileged "agent" user.
set -e
DATA_DIR="$(dirname "${TASKS_DB:-/app/data/tasks.db}")"
LOG_DIR="$(dirname "${LOG_FILE:-/app/logs/agent.log}")"
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR" "$LOG_DIR"
  chown -R agent:agent "$DATA_DIR" "$LOG_DIR" 2>/dev/null || true
  if command -v setpriv >/dev/null 2>&1; then
    exec setpriv --reuid=agent --regid=agent --init-groups "$@"
  fi
  echo "warning: setpriv not found, running as root" >&2
fi
exec "$@"
