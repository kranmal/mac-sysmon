# Mac Sysmon

A lightweight, **transparent desktop system-monitor overlay for macOS** (Apple Silicon). A port of [linux-sysmon](https://github.com/kranmal/linux-sysmon): same look, same single Python + Qt (PySide6) file, with the Linux `/sys` readers swapped for macOS sources. No sudo needed.

## Features

| Section | Shows |
|---------|-------|
| **Clock** | time and date |
| **Service toggle** *(optional)* | status of a launchd **user agent**, its RAM use, and a **START/STOP** button |
| **CPU** | total usage + 60 s graph, hottest die temperature, load average, one bar per core split into efficiency and performance clusters |
| **RAM** | used / total, swap, bar |
| **GPU** | usage + graph, memory in use (unified memory, so shared with RAM) |
| **Power** *(laptops)* | battery %, charging state, watts in/out, time remaining |
| **Temps / Storage** | SSD and battery temperature, macOS thermal state; every physical disk with capacity and, per volume, % used, free and total. External drives appear automatically; mounted disk images are ignored |
| **Network** | Wi-Fi signal % and link rate, IP and rates per active interface, wired Ethernet when connected, combined download/upload with graph |

Bars and numbers turn **yellow above 60 % and red above 85 %** (temperatures: yellow above 60 °C, red above 80 °C).

Interaction: **drag** to move it (position is remembered), **right-click** for opacity presets, *Float on top*, click-through mode and quit. It sits just below normal windows, on every Space, with no Dock icon.

## Install and run

```bash
./install.sh                 # creates .venv and installs PySide6, psutil, pyobjc
./run.sh                     # start it in the background
./install.sh --autostart     # also start at login (LaunchAgent)
./install.sh --uninstall     # remove the LaunchAgent
```

Stop it from the right-click menu, or `pkill -f mac-sysmon/sysmon.py`.

## Options

```
sysmon.py [--service-label LABEL] [--service-title TEXT]
          [--service-health-url URL] [--service-process NAME] [--demo]
```

| Option | Meaning |
|--------|---------|
| `--service-label LABEL` | Show a toggle for this launchd user agent (`gui/<uid>/LABEL`). Omit to hide the section |
| `--service-title TEXT` | Section heading (default `SERVICE`) |
| `--service-health-url URL` | If set, `RUNNING` means this URL returns HTTP 200; until then the state is `LOADING` |
| `--service-process NAME` | Process name whose RAM use is shown next to the status |
| `--demo` | Fake data, for screenshots and testing |

Each also exists as an environment variable (`SYSMON_SERVICE_LABEL`, `SYSMON_SERVICE_TITLE`, ...). The button runs `launchctl kickstart` / `launchctl kill SIGTERM`; an agent with `KeepAlive` will be restarted by launchd after a stop.

## Where each metric comes from

| Metric | Source |
|--------|--------|
| CPU usage, per core, RAM, swap, network rates | `psutil` |
| E/P core split | `sysctl hw.perflevel1.logicalcpu` (efficiency cores are numbered first) |
| Temperatures | IOHID thermal sensors (`PMU tdie*`, `NAND`, `gas gauge battery`), a private but root-free API |
| GPU usage and memory | `ioreg -c IOAccelerator` (`PerformanceStatistics`) |
| Battery | `ioreg -n AppleSmartBattery` |
| Thermal state | `NSProcessInfo.thermalState` |
| Disks | `diskutil info -plist` + `statvfs` (the Data volume is measured for "Macintosh HD") |
| Wi-Fi | CoreWLAN |

## Known limitations

- **Wi-Fi name is hidden.** macOS only reveals the SSID to apps with Location Services permission, so the interface name (`en0`) is shown instead. Signal and link rate work.
- Tested on one machine: MacBook Air M4, macOS 27. Intel Macs have no IOHID temperature sensors or E/P split; those parts show `-` or are simplified.
- No fan speed or per-rail power (needs SMC access / `powermetrics` with sudo).
- Settings live in `~/Library/Application Support/mac-sysmon/config.json`.

## Testing

```bash
.venv/bin/python tests/run_tests.py
```

## License

[MIT](LICENSE)
