#!/usr/bin/env bash
# Start Mac Sysmon in the background (detached from the terminal).
here="$(cd "$(dirname "$0")" && pwd)"
nohup "$here/.venv/bin/python" "$here/sysmon.py" "$@" >/dev/null 2>&1 &
