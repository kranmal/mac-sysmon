#!/usr/bin/env bash
# Set up / remove Mac Sysmon for the current user.
#   ./install.sh [--autostart] [sysmon options...]   e.g. --service-label com.example.llama
#   ./install.sh --uninstall
set -euo pipefail
LABEL="com.kranmal.mac-sysmon"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
here="$(cd "$(dirname "$0")" && pwd)"

[[ "$(uname -s)" == "Darwin" ]] || { echo "Mac Sysmon only supports macOS." >&2; exit 1; }

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$AGENT"
  echo "Removed $AGENT (the .venv folder and your settings are left alone)"; exit 0
fi

autostart=0; extra=()
for a in "$@"; do
  if [[ "$a" == "--autostart" ]]; then autostart=1; else extra+=("$a"); fi
done

if [[ ! -x "$here/.venv/bin/python" ]]; then
  if command -v uv >/dev/null; then uv venv -q --python 3.13 "$here/.venv"
  else python3 -m venv "$here/.venv"; fi
fi
# optional: helper app that lets the monitor show the Wi-Fi name (needs Xcode command line tools)
if command -v swiftc >/dev/null; then "$here/helper/build.sh" || echo "(Wi-Fi name helper not built)"; fi
if command -v uv >/dev/null; then uv pip install -q --python "$here/.venv/bin/python" -r "$here/requirements.txt"
else "$here/.venv/bin/python" -m pip install -q -r "$here/requirements.txt"; fi
echo "Ready. Run it with: $here/run.sh"

if [[ $autostart -eq 1 ]]; then
  mkdir -p "$(dirname "$AGENT")"
  args="    <string>$here/.venv/bin/python</string>
    <string>$here/sysmon.py</string>"
  for a in "${extra[@]+"${extra[@]}"}"; do args+="
    <string>$a</string>"; done
  cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
$args
  </array>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Interactive</string>
</dict></plist>
PLIST
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  # bootout is asynchronous: retry until the old instance has gone
  for _ in 1 2 3 4 5; do
    launchctl bootstrap "gui/$(id -u)" "$AGENT" 2>/dev/null && break
    sleep 1
  done
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null || { echo "Could not load $AGENT" >&2; exit 1; }
  echo "Autostart at login: $AGENT (started now)"
fi
