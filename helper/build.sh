#!/usr/bin/env bash
# Builds .build/SysmonWiFi.app (needs Xcode command line tools: xcode-select --install).
set -euo pipefail
cd "$(dirname "$0")/.."
APP=".build/SysmonWiFi.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
swiftc -O helper/wifi-ssid.swift -o "$APP/Contents/MacOS/SysmonWiFi"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.kranmal.mac-sysmon.wifi</string>
  <key>CFBundleName</key><string>Mac Sysmon Wi-Fi</string>
  <key>CFBundleExecutable</key><string>SysmonWiFi</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
  <key>NSLocationUsageDescription</key>
  <string>Mac Sysmon shows the name of the Wi-Fi network you are connected to. macOS only reveals it to apps with Location access.</string>
  <key>NSLocationWhenInUseUsageDescription</key>
  <string>Mac Sysmon shows the name of the Wi-Fi network you are connected to. macOS only reveals it to apps with Location access.</string>
</dict></plist>
PLIST
codesign --force --sign - "$APP" >/dev/null
echo "Built $APP"
