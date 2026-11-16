#!/usr/bin/env python3
"""Mac Sysmon: transparent desktop system monitor overlay for macOS (Apple Silicon).

A port of linux-sysmon. Shows CPU (per core, E/P clusters), RAM, GPU, battery/power,
temperatures, storage, Wi-Fi/Ethernet and, optionally, a start/stop toggle for an
app or launchd user agent (e.g. a local LLM server such as Ollama). No sudo needed.

Drag with left mouse. Right-click for menu (opacity, float on top, click-through, quit).
Run `sysmon.py --help` for options.
"""
import argparse
import ctypes
import ctypes.util
import json
import math
import os
import plistlib
import re
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque

import psutil
from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QGuiApplication, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

HIST = 60  # samples (1/s)
W = 300
FONT = "Menlo"
# Optional service toggle (launchd *user* agent label). Empty = section hidden.
SVC_LABEL = os.environ.get("SYSMON_SERVICE_LABEL", "")
# ...or a macOS app (e.g. "Ollama"), started with `open` and stopped by quitting it.
SVC_APP = os.environ.get("SYSMON_SERVICE_APP", "")
SVC_URL = os.environ.get("SYSMON_SERVICE_HEALTH_URL", "")     # optional HTTP health check
SVC_PROC = os.environ.get("SYSMON_SERVICE_PROCESS", "")       # optional process name for RAM
SVC_TITLE = os.environ.get("SYSMON_SERVICE_TITLE", "SERVICE")
DEMO = False     # --demo: fake data (for screenshots and tests)
CONFIG = os.path.expanduser("~/Library/Application Support/mac-sysmon/config.json")

LEVEL_BELOW, LEVEL_TOP = -1, 3   # NSWindow levels: just under normal windows / floating
THERMAL = ("Nominal", "Fair", "Serious", "Critical")
NET_SKIP = ("lo", "utun", "awdl", "llw", "bridge", "anpi", "ap", "gif", "stf")


def human(bps):
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bps < 1024:
            return f"{bps:5.1f} {unit}"
        bps /= 1024
    return f"{bps:5.1f} TB/s"


def fmt_size(b):
    g = b / 2**30
    if g >= 1000:
        return f"{g / 1024:.2f}T"
    if g >= 10:
        return f"{g:.0f}G"
    if g >= 1:
        return f"{g:.1f}G"
    return f"{b / 2**20:.0f}M"


def run_plist(cmd, timeout=3):
    """Run a command that prints a plist; return the parsed object or None."""
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout).stdout
        return plistlib.loads(out) if out else None
    except Exception:
        return None


def sysctl_int(name, default=0):
    try:
        return int(subprocess.run(["sysctl", "-n", name], capture_output=True,
                                  text=True, timeout=2).stdout.strip())
    except Exception:
        return default


# ---- temperatures (IOHID thermal sensors, readable without root) ----
class TempSensors:
    """Apple Silicon temperature sensors via the IOHIDEventSystem client API."""

    def __init__(self):
        self.services = []
        try:
            vp = ctypes.c_void_p
            cf = self.cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
            io = self.io = ctypes.CDLL(ctypes.util.find_library("IOKit"))
            cf.CFStringCreateWithCString.restype = vp
            cf.CFStringCreateWithCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_uint32]
            cf.CFStringGetCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
            cf.CFNumberCreate.restype = vp
            cf.CFNumberCreate.argtypes = [vp, ctypes.c_long, vp]
            cf.CFDictionaryCreate.restype = vp
            cf.CFDictionaryCreate.argtypes = [vp, vp, vp, ctypes.c_long, vp, vp]
            cf.CFArrayGetCount.restype = ctypes.c_long
            cf.CFArrayGetCount.argtypes = [vp]
            cf.CFArrayGetValueAtIndex.restype = vp
            cf.CFArrayGetValueAtIndex.argtypes = [vp, ctypes.c_long]
            cf.CFRelease.argtypes = [vp]
            io.IOHIDEventSystemClientCreate.restype = vp
            io.IOHIDEventSystemClientCreate.argtypes = [vp]
            io.IOHIDEventSystemClientSetMatching.argtypes = [vp, vp]
            io.IOHIDEventSystemClientCopyServices.restype = vp
            io.IOHIDEventSystemClientCopyServices.argtypes = [vp]
            io.IOHIDServiceClientCopyProperty.restype = vp
            io.IOHIDServiceClientCopyProperty.argtypes = [vp, vp]
            io.IOHIDServiceClientCopyEvent.restype = vp
            io.IOHIDServiceClientCopyEvent.argtypes = [vp, ctypes.c_int64, ctypes.c_int32,
                                                       ctypes.c_int64]
            io.IOHIDEventGetFloatValue.restype = ctypes.c_double
            io.IOHIDEventGetFloatValue.argtypes = [vp, ctypes.c_int32]

            def cfstr(s):
                return cf.CFStringCreateWithCString(None, s.encode(), 0x08000100)

            def cfnum(v):
                i = ctypes.c_int32(v)
                return cf.CFNumberCreate(None, 3, ctypes.byref(i))

            keys = (vp * 2)(cfstr("PrimaryUsagePage"), cfstr("PrimaryUsage"))
            vals = (vp * 2)(cfnum(0xFF00), cfnum(5))   # Apple vendor page, temperature sensor
            match = cf.CFDictionaryCreate(
                None, keys, vals, 2,
                ctypes.addressof(vp.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")),
                ctypes.addressof(vp.in_dll(cf, "kCFTypeDictionaryValueCallBacks")))
            client = io.IOHIDEventSystemClientCreate(None)
            io.IOHIDEventSystemClientSetMatching(client, match)
            arr = io.IOHIDEventSystemClientCopyServices(client)
            product = cfstr("Product")
            buf = ctypes.create_string_buffer(128)
            for i in range(cf.CFArrayGetCount(arr) if arr else 0):
                svc = cf.CFArrayGetValueAtIndex(arr, i)
                name = io.IOHIDServiceClientCopyProperty(svc, product)
                if name and cf.CFStringGetCString(name, buf, 128, 0x08000100):
                    self.services.append((buf.value.decode(errors="replace"), svc))
        except Exception:
            self.services = []

    def read(self):
        """Return {"cpu": max die temp, "ssd": .., "batt": ..} (missing keys = no sensor)."""
        die, ssd, batt = [], [], []
        for name, svc in self.services:
            ev = self.io.IOHIDServiceClientCopyEvent(svc, 15, 0, 0)
            if not ev:
                continue
            v = self.io.IOHIDEventGetFloatValue(ev, 15 << 16)
            self.cf.CFRelease(ev)
            if not 0 < v < 130:
                continue
            if "tdie" in name:
                die.append(v)
            elif name.startswith("NAND"):
                ssd.append(v)
            elif "battery" in name:
                batt.append(v)
        out = {}
        if die:
            out["cpu"] = max(die)
        if ssd:
            out["ssd"] = max(ssd)
        if batt:
            out["batt"] = max(batt)
        return out


# ---- disks ----
_dinfo = {}


def disk_info(target):
    if target not in _dinfo:
        _dinfo[target] = run_plist(["diskutil", "info", "-plist", target]) or {}
    return _dinfo[target]


def scan_disks():
    """Return [{name, model, size, parts:[{label, mount, total, free, pct}]}]."""
    mounts = ["/"] + sorted(p.mountpoint for p in psutil.disk_partitions()
                            if p.mountpoint.startswith("/Volumes/"))
    disks = {}
    for mp in mounts:
        info = disk_info(mp)
        if not info or info.get("BusProtocol") == "Disk Image" or mp == "/Volumes/Recovery":
            continue
        stores = info.get("APFSPhysicalStores") or []
        dev = stores[0].get("APFSPhysicalStore", "") if stores else info.get("ParentWholeDisk", "")
        m = re.match(r"disk\d+", dev or "")
        whole = m.group(0) if m else (dev or "disk?")
        if whole not in disks:
            wi = disk_info(whole)
            disks[whole] = {"name": whole, "model": (wi.get("MediaName") or "").strip(),
                            "size": int(wi.get("TotalSize") or 0), "parts": []}
        # "/" is the sealed system snapshot; the Data volume shares its container.
        usage_path = "/System/Volumes/Data" if mp == "/" else mp
        part = {"label": info.get("VolumeName") or mp, "mount": mp, "pct": None}
        try:
            st = os.statvfs(usage_path)
            total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
            part.update(total=total, free=free, pct=100 * (1 - free / total) if total else None)
        except OSError:
            pass
        disks[whole]["parts"].append(part)
    return list(disks.values())


def demo_disks():
    gb = 2**30
    return [
        {"name": "disk0", "model": "APPLE SSD Demo", "size": 500 * gb, "parts": [
            {"label": "Macintosh HD", "mount": "/", "total": 494 * gb, "free": 310 * gb,
             "pct": 37.2}]},
        {"name": "disk4", "model": "Demo USB SSD", "size": 1000 * gb, "parts": [
            {"label": "Backup", "mount": "/Volumes/Backup", "total": 1000 * gb,
             "free": 140 * gb, "pct": 86.0}]},
    ]


class Monitor(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnBottomHint
                            | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("sysmon")
        self.demo = DEMO
        self.svc_enabled = bool(SVC_LABEL or SVC_APP)
        self.cfg = {}
        if not self.demo:
            try:
                self.cfg = json.load(open(CONFIG))
            except Exception:
                self.cfg = {}
        self.bg_alpha = int(self.cfg.get("alpha", 90))
        self.on_top = bool(self.cfg.get("on_top", False))
        self.click_through = False
        self.resize(W, 600)

        self.n_cpu = psutil.cpu_count() or 1
        # Apple Silicon numbers efficiency cores first, then performance cores.
        self.n_eff = min(sysctl_int("hw.perflevel1.logicalcpu"), self.n_cpu)
        self.temps = TempSensors()
        self.wifi = None
        self.wifi_if = None
        try:
            import CoreWLAN
            self.wifi = CoreWLAN.CWWiFiClient.sharedWiFiClient().interface()
            self.wifi_if = str(self.wifi.interfaceName()) if self.wifi else None
        except Exception:
            pass
        try:
            from Foundation import NSProcessInfo
            self.procinfo = NSProcessInfo.processInfo()
        except Exception:
            self.procinfo = None

        self.hist = {k: deque([0.0] * HIST, maxlen=HIST)
                     for k in ("cpu", "ram", "gpu", "down", "up")}
        self.cores = [0.0] * self.n_cpu
        self.s = {}
        self.rates = {}
        self.eth = []
        self._net = psutil.net_io_counters(pernic=True)
        self._net_t = time.time()
        psutil.cpu_percent(percpu=True)
        self.disks = []
        self._disk_t = 0.0
        self._slow_t = 0.0
        self.svc_state = "off"   # off | loading | running | stopping | failed
        self.svc_busy_until = 0.0
        self.svc_ram = 0.0
        self.btn_rect = QRectF(W - 14 - 64, 0, 64, 20)
        if self.svc_enabled and not self.demo:
            threading.Thread(target=self.svc_poll, daemon=True).start()

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.save_cfg)
        t = QTimer(self)
        t.timeout.connect(self.sample)
        t.start(1000)
        self.sample()

    # ---- window placement (macOS) ----
    def nswindow(self):
        if QGuiApplication.platformName() != "cocoa":
            return None
        try:
            import objc
            return objc.objc_object(c_void_p=int(self.winId())).window()
        except Exception:
            return None

    def apply_native(self):
        win = self.nswindow()
        if win is None:
            return
        win.setLevel_(LEVEL_TOP if self.on_top else LEVEL_BELOW)
        # all Spaces | stays put in Mission Control | skipped by Cmd-` window cycling
        win.setCollectionBehavior_(1 | 16 | 64)
        win.setHidesOnDeactivate_(False)
        win.setIgnoresMouseEvents_(self.click_through)

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_placed", False):
            self._placed = True
            scr = QGuiApplication.primaryScreen().availableGeometry()
            pos = QPoint(scr.right() - W - 20, scr.top() + 20)
            if "x" in self.cfg and "y" in self.cfg:
                saved = QPoint(int(self.cfg["x"]), int(self.cfg["y"]))
                if any(s.geometry().contains(saved) for s in QGuiApplication.screens()):
                    pos = saved
            self.move(pos)
        self.apply_native()

    def moveEvent(self, e):
        super().moveEvent(e)
        if getattr(self, "_placed", False):
            self._save_timer.start(800)

    def save_cfg(self):
        if self.demo or QGuiApplication.platformName() != "cocoa":
            return
        self.cfg.update(x=self.x(), y=self.y(), alpha=self.bg_alpha, on_top=self.on_top)
        try:
            os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
            json.dump(self.cfg, open(CONFIG, "w"))
        except OSError:
            pass

    # ---- service (launchd user agent) ----
    def svc_target(self):
        return f"gui/{os.getuid()}/{SVC_LABEL}"

    @staticmethod
    def app_procs():
        """Processes belonging to the app: "Ollama" also matches its `ollama` helpers."""
        want = SVC_APP.lower()
        return [pr for pr in psutil.process_iter(["name", "memory_info"])
                if (pr.info["name"] or "").lower() == want]

    def svc_poll(self):
        while True:
            try:
                procs = []
                if SVC_APP:
                    procs = self.app_procs()
                    st = "running" if procs else "off"
                else:
                    r = subprocess.run(["launchctl", "print", self.svc_target()],
                                       capture_output=True, text=True, timeout=3)
                    m = re.search(r"^\s*state = (.+)$", r.stdout, re.M)
                    st = m.group(1).strip() if (r.returncode == 0 and m) else "off"
                if st == "running":
                    state = "running"
                    if SVC_URL:
                        try:
                            with urllib.request.urlopen(SVC_URL, timeout=0.5) as resp:
                                state = "running" if resp.status == 200 else "loading"
                        except Exception:
                            state = "loading"
                elif st in ("spawn scheduled", "starting"):
                    state = "loading"
                else:
                    state = "off"
                ram = 0.0
                if SVC_PROC:
                    procs = [pr for pr in psutil.process_iter(["name", "memory_info"])
                             if pr.info["name"] == SVC_PROC]
                for pr in procs:
                    if pr.info["memory_info"]:
                        ram += pr.info["memory_info"].rss / 2**30
                if time.time() > self.svc_busy_until:
                    self.svc_state = state
                self.svc_ram = ram
            except Exception:
                pass
            time.sleep(1.5)

    def svc_toggle(self):
        on = self.svc_state in ("running", "loading")
        if SVC_APP and on:
            for pr in self.app_procs():          # same as quitting the app
                try:
                    pr.terminate()
                except psutil.Error:
                    pass
        else:
            cmd = (["open", "-g", "-j", "-a", SVC_APP] if SVC_APP
                   else ["launchctl", "kill", "SIGTERM", self.svc_target()] if on
                   else ["launchctl", "kickstart", self.svc_target()])
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.svc_state = "stopping" if on else "loading"
        self.svc_busy_until = time.time() + 3
        self.update()

    def apply_demo(self):
        s, t = self.s, time.time()
        self.cores = [max(2, 45 + 40 * math.sin(t * 0.7 + i)) for i in range(len(self.cores))]
        s.update(cpu=sum(self.cores) / len(self.cores), cpu_t=58,
                 ram=42.0, ram_used=6.7, ram_total=16, swap_used=0.0,
                 gpu=55 + 30 * math.sin(t * 0.5), gpu_mem=3.1, gpu_name="Apple M4",
                 batt={"pct": 74, "charging": False, "plugged": False, "watts": -9.4,
                       "mins": 312, "cycles": 38},
                 ssd_t=38, batt_t=29, thermal=0,
                 wifi={"ssid": "HomeWiFi", "sig": 72, "rate": 432, "ip": "192.168.1.20"},
                 down=2.4e6 * (1 + math.sin(t)), up=3e5 * (1 + math.cos(t)))
        self.wifi_if = self.wifi_if or "en0"
        self.rates = {self.wifi_if: (s["down"], s["up"])}
        self.eth = []
        self.svc_state, self.svc_ram = "running", 7.2

    # ---- data ----
    def read_gpu(self):
        for d in run_plist(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator", "-a"], 2) or []:
            ps = d.get("PerformanceStatistics") or {}
            if "Device Utilization %" in ps:
                self.s["gpu"] = float(ps["Device Utilization %"])
                self.s["gpu_mem"] = ps.get("In use system memory", 0) / 2**30
                self.s["gpu_name"] = d.get("model") or "GPU"
                return

    def read_battery(self):
        self.s["batt"] = None
        for d in run_plist(["ioreg", "-r", "-n", "AppleSmartBattery", "-a"], 2) or []:
            if "CurrentCapacity" not in d:
                continue
            amps = int(d.get("Amperage", 0))
            if amps >= 2**63:                      # ioreg prints it unsigned
                amps -= 2**64
            mins = d.get("TimeRemaining")
            self.s["batt"] = {
                "pct": float(d.get("CurrentCapacity", 0)),
                "charging": bool(d.get("IsCharging")),
                "plugged": bool(d.get("ExternalConnected")),
                "watts": amps * int(d.get("Voltage", 0)) / 1e6,
                "mins": mins if isinstance(mins, int) and 0 < mins < 6000 else None,
                "cycles": d.get("CycleCount"),
            }
            return

    def read_wifi(self):
        self.s["wifi"] = None
        w = self.wifi
        try:
            if not (w and w.powerOn()):
                return
            rssi = int(w.rssiValue())
            ip = self.ipv4(self.wifi_if)
            if not rssi or not ip:
                return
            ssid = w.ssid()   # None unless the app has Location Services permission
            self.s["wifi"] = {"ssid": str(ssid) if ssid else None,
                              "sig": float(min(max(2 * (rssi + 100), 0), 100)),
                              "rate": float(w.transmitRate() or 0), "ip": ip}
        except Exception:
            pass

    @staticmethod
    def ipv4(nic):
        for a in psutil.net_if_addrs().get(nic, []):
            if a.family.name == "AF_INET":
                return a.address
        return ""

    def sample(self):
        s = self.s
        now = time.time()
        self.cores = psutil.cpu_percent(percpu=True)
        s["cpu"] = sum(self.cores) / len(self.cores)
        s["load"] = os.getloadavg()[0]

        vm = psutil.virtual_memory()
        s["ram"] = vm.percent
        s["ram_used"] = (vm.total - vm.available) / 2**30
        s["ram_total"] = vm.total / 2**30
        s["swap_used"] = psutil.swap_memory().used / 2**30

        if not self.demo:
            self.read_gpu()
            t = self.temps.read()
            s["cpu_t"], s["ssd_t"], s["batt_t"] = t.get("cpu"), t.get("ssd"), t.get("batt")
            if now - self._slow_t > 5:
                self._slow_t = now
                self.read_battery()
                self.read_wifi()
                s["thermal"] = int(self.procinfo.thermalState()) if self.procinfo else None
                stats = psutil.net_if_stats()
                self.eth = []
                for nic in sorted(stats):
                    if nic.startswith("en") and nic != self.wifi_if and stats[nic].isup:
                        ip = self.ipv4(nic)
                        if ip:
                            self.eth.append({"nic": nic, "speed": stats[nic].speed, "ip": ip})

        if self.demo:
            self.disks = demo_disks()
        elif now - self._disk_t > 10 or not self.disks:
            self._disk_t = now
            self.disks = scan_disks()

        cur = psutil.net_io_counters(pernic=True)
        dt = max(now - self._net_t, 1e-3)
        down = up = 0.0
        self.rates = {}
        for nic, c in cur.items():
            if nic.startswith(NET_SKIP):
                continue
            p = self._net.get(nic)
            if p:
                d = max(c.bytes_recv - p.bytes_recv, 0) / dt
                u = max(c.bytes_sent - p.bytes_sent, 0) / dt
                down += d
                up += u
                self.rates[nic] = (d, u)
        self._net, self._net_t = cur, now
        s["down"], s["up"] = down, up

        if self.demo:
            self.apply_demo()
        for k in ("cpu", "ram", "gpu"):
            self.hist[k].append(s.get(k) or 0)
        self.hist["down"].append(s["down"])
        self.hist["up"].append(s["up"])
        self.update()

    # ---- drawing ----
    @staticmethod
    def color(pct):
        if pct < 60:
            return QColor("#5fe3a1")
        if pct < 85:
            return QColor("#ffcf5c")
        return QColor("#ff6b6b")

    @staticmethod
    def temp_color(t):
        if t is None:
            return QColor("#aaaaaa")
        if t < 60:
            return QColor("#5fe3a1")
        if t < 80:
            return QColor("#ffcf5c")
        return QColor("#ff6b6b")

    def text(self, p, x, y, s, color=QColor("white"), bold=False, size=10,
             align=Qt.AlignLeft, width=0):
        f = QFont(FONT, size)
        f.setBold(bold)
        p.setFont(f)
        r = QRectF(x, y - 14, width or 300, 18)
        p.setPen(QColor(0, 0, 0, 200))
        p.drawText(r.translated(1, 1), align | Qt.AlignVCenter, s)
        p.setPen(color)
        p.drawText(r, align | Qt.AlignVCenter, s)

    def rtext(self, p, y, s, color=QColor("#cccccc"), **kw):
        self.text(p, 0, y, s, color, align=Qt.AlignRight, width=W - 14, **kw)

    def bar(self, p, x, y, w, pct, color):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(QRectF(x, y, w, 6), 3, 3)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(x, y, w * min(max(pct, 0), 100) / 100, 6), 3, 3)

    def spark(self, p, x, y, w, h, data, color, maxv=100.0):
        data = list(data)
        maxv = max(maxv, 1e-9)
        path = QPainterPath()
        n = len(data)
        for i, v in enumerate(data):
            px = x + w * i / (n - 1)
            py = y + h - h * min(v / maxv, 1.0)
            path.moveTo(px, py) if i == 0 else path.lineTo(px, py)
        fill = QPainterPath(path)
        fill.lineTo(x + w, y + h)
        fill.lineTo(x, y + h)
        fill.closeSubpath()
        c = QColor(color)
        c.setAlpha(50)
        p.setPen(Qt.NoPen)
        p.fillPath(fill, c)
        p.setPen(QPen(color, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def section(self, p, y, title):
        self.text(p, 14, y, title, QColor("#9ad1ff"), bold=True, size=9)
        f = QFont(FONT, 9)
        f.setBold(True)
        x0 = 14 + QFontMetrics(f).horizontalAdvance(title) + 8
        p.setPen(QPen(QColor(255, 255, 255, 50), 1))
        p.drawLine(int(x0), int(y - 5), W - 14, int(y - 5))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        s = self.s
        grey, dim = QColor("#cccccc"), QColor("#8a8f98")
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(10, 12, 18, self.bg_alpha))
        p.drawRoundedRect(QRectF(0, 0, W, self.height()), 14, 14)

        iw = W - 28
        y = 26
        self.text(p, 14, y, time.strftime("%H:%M:%S"), size=16, bold=True)
        self.rtext(p, y, time.strftime("%a %d %b"))

        if self.svc_enabled:
            y = self.paint_service(p, y)

        # CPU
        y += 30
        self.section(p, y, "CPU")
        y += 20
        c = self.color(s["cpu"])
        self.text(p, 14, y, f"{s['cpu']:5.1f}%", c, bold=True, size=13)
        t = s.get("cpu_t")
        self.rtext(p, y, (f"{t:.0f}°C  " if t else "") + f"load {s['load']:.2f}",
                   self.temp_color(t) if t else grey)
        y += 8
        self.spark(p, 14, y, iw, 30, self.hist["cpu"], c)
        y += 38
        n = max(len(self.cores), 1)
        split = self.n_eff if 0 < self.n_eff < n else 0
        gap, group_gap = (3 if n <= 16 else 1), (8 if split else 0)
        bw = (iw - (n - 1) * gap - group_gap) / n
        for i, v in enumerate(self.cores):
            bx = 14 + i * (bw + gap) + (group_gap if split and i >= split else 0)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawRoundedRect(QRectF(bx, y, bw, 22), 2, 2)
            p.setBrush(self.color(v))
            hh = 22 * v / 100
            p.drawRoundedRect(QRectF(bx, y + 22 - hh, bw, hh), 2, 2)
        y += 22
        if split:
            y += 14
            ew = split * (bw + gap) - gap
            self.text(p, 14, y, f"{split} efficiency", dim, size=8,
                      align=Qt.AlignCenter, width=ew)
            self.text(p, 14 + ew + gap + group_gap, y, f"{n - split} performance", dim,
                      size=8, align=Qt.AlignCenter, width=iw - ew - gap - group_gap)
        y += 16

        # RAM
        y += 6
        self.section(p, y, "RAM")
        y += 20
        c = self.color(s["ram"])
        self.text(p, 14, y, f"{s['ram_used']:4.1f}/{s['ram_total']:.0f} GB", c, bold=True)
        self.rtext(p, y, f"swap {s['swap_used']:.1f}G")
        y += 8
        self.bar(p, 14, y, iw, s["ram"], c)
        y += 26

        # GPU
        self.section(p, y, "GPU")
        y += 20
        if s.get("gpu") is not None:
            c = self.color(s["gpu"])
            self.text(p, 14, y, f"{s['gpu']:5.1f}%", c, bold=True, size=13)
            self.rtext(p, y, f"{s.get('gpu_name', '')}  {s.get('gpu_mem', 0):.1f}G mem")
            y += 8
            self.spark(p, 14, y, iw, 30, self.hist["gpu"], c)
            y += 48
        else:
            self.text(p, 14, y, "no GPU statistics available", QColor("#aaaaaa"))
            y += 30

        # POWER
        b = s.get("batt")
        if b:
            self.section(p, y, "POWER")
            y += 20
            c = self.color(100 - b["pct"])
            state = ("charging" if b["charging"] else "plugged in" if b["plugged"]
                     else "on battery")
            self.text(p, 14, y, f"{b['pct']:.0f}% {state}", c, bold=True)
            right = []
            if abs(b["watts"]) >= 0.05:
                right.append(f"{b['watts']:+.1f}W")
            if b["mins"]:
                right.append(f"{b['mins'] // 60}h{b['mins'] % 60:02d}")
            self.rtext(p, y, "  ".join(right))
            y += 8
            self.bar(p, 14, y, iw, b["pct"], c)
            y += 26

        # TEMPS / STORAGE
        self.section(p, y, "TEMPS / STORAGE")
        y += 20
        for i, (name, t) in enumerate((("SSD", s.get("ssd_t")), ("Batt", s.get("batt_t")))):
            self.text(p, 14 + i * 92, y, f"{name} " + (f"{t:.0f}°C" if t else "-"),
                      self.temp_color(t))
        th = s.get("thermal")
        if th is not None:
            self.rtext(p, y, THERMAL[min(th, 3)],
                       (QColor("#5fe3a1"), QColor("#ffcf5c"), QColor("#ff6b6b"),
                        QColor("#ff6b6b"))[min(th, 3)])
        y += 6
        for d in self.disks:
            y += 20
            model = f" {d['model'][:22]}" if d["model"] else ""
            self.text(p, 14, y, f"{d['name']}{model}", QColor("#cfd6e0"), bold=True, size=9)
            self.rtext(p, y, fmt_size(d["size"]), QColor("#cfd6e0"), size=9)
            for pt in d["parts"]:
                y += 22
                pct = pt.get("pct")
                if pct is None:
                    self.text(p, 14, y, pt["label"], dim, size=9)
                    y += 6
                    continue
                c = self.color(pct)
                self.text(p, 14, y, f"{pt['label'][:14]} {pct:.0f}%", c, size=9)
                self.rtext(p, y, f"{fmt_size(pt['free'])} free / {fmt_size(pt['total'])}",
                           size=9)
                y += 6
                self.bar(p, 14, y, iw, pct, c)
        y += 26

        # NETWORK
        self.section(p, y, "NETWORK")
        y += 20
        down_c, up_c = QColor("#7fd0ff"), QColor("#ffb86b")
        wf = s.get("wifi")
        if wf:
            wc = self.color(100 - wf["sig"])
            name = wf["ssid"][:16] if wf["ssid"] else (self.wifi_if or "")
            self.text(p, 14, y, f"WiFi {name}", wc, bold=True)
            rate = f"{wf['rate']:.0f}Mb/s  " if wf["rate"] else ""
            self.rtext(p, y, f"{rate}{wf['sig']:.0f}%", wc)
            y += 20
            self.paint_nic_rates(p, y, wf["ip"], self.wifi_if)
        else:
            self.text(p, 14, y, "WiFi: down", QColor("#aaaaaa"))
        y += 22
        for e in self.eth:
            ec = QColor("#5fe3a1")
            sp = e["speed"]
            self.text(p, 14, y, f"Eth {e['nic']}", ec, bold=True)
            self.rtext(p, y, f"{sp / 1000:g} Gb/s" if sp >= 1000 else
                       f"{sp:.0f} Mb/s" if sp else "up", ec)
            y += 20
            self.paint_nic_rates(p, y, e["ip"], e["nic"])
            y += 22
        peak = max(max(self.hist["down"]), max(self.hist["up"]), 100e3)
        self.text(p, 14, y, f"Σ ↓ {human(s['down'])}", down_c)
        self.rtext(p, y, f"Σ ↑ {human(s['up'])}", up_c)
        y += 8
        self.spark(p, 14, y, iw, 28, self.hist["down"], down_c, peak)
        self.spark(p, 14, y, iw, 28, self.hist["up"], up_c, peak)
        y += 28 + 14
        p.end()
        if y != self.height():          # content changed (disk added, cable plugged, ...)
            QTimer.singleShot(0, lambda h=y: (self.resize(W, h), self.update()))

    def paint_nic_rates(self, p, y, ip, nic):
        d, u = self.rates.get(nic, (0, 0))
        self.text(p, 14, y, ip, QColor("#cccccc"), size=9)
        self.text(p, 130, y, f"↓{human(d).strip()}", QColor("#7fd0ff"), size=9)
        self.rtext(p, y, f"↑{human(u).strip()}", QColor("#ffb86b"), size=9)

    def paint_service(self, p, y):
        y += 30
        self.section(p, y, SVC_TITLE)
        y += 24
        st = self.svc_state
        col = {"running": QColor("#5fe3a1"), "loading": QColor("#ffcf5c"),
               "stopping": QColor("#ffcf5c"), "failed": QColor("#ff6b6b"),
               "off": QColor("#8a8f98")}[st]
        self.text(p, 14, y, f"● {st.upper()}", col, bold=True, size=11)
        if self.svc_ram > 0.05:
            self.text(p, 116, y, f"{self.svc_ram:.1f}G RAM", QColor("#cccccc"))
        self.btn_rect = QRectF(W - 14 - 64, y - 14, 64, 20)
        on = st in ("running", "loading")
        bc = QColor("#ff6b6b") if on else QColor("#5fe3a1")
        p.setPen(QPen(bc, 1.2))
        fc = QColor(bc)
        fc.setAlpha(45)
        p.setBrush(fc)
        p.drawRoundedRect(self.btn_rect, 6, 6)
        self.text(p, self.btn_rect.x(), y, "STOP" if on else "START", bc, bold=True,
                  size=9, align=Qt.AlignCenter, width=64)
        return y + 4

    # ---- interaction ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self.svc_enabled and self.btn_rect.contains(e.position()):
                self.svc_toggle()
            elif self.windowHandle():
                self.windowHandle().startSystemMove()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        for label, a in (("Opacity: clear", 0), ("Opacity: light", 60),
                         ("Opacity: medium", 130), ("Opacity: dark", 200)):
            act = QAction(label, m)
            act.triggered.connect(lambda _=False, a=a: self.set_alpha(a))
            m.addAction(act)
        m.addSeparator()
        top = QAction("Float on top", m)
        top.setCheckable(True)
        top.setChecked(self.on_top)
        top.triggered.connect(self.set_on_top)
        m.addAction(top)
        ct = QAction("Click-through (restart to undo)", m)
        ct.triggered.connect(self.enable_click_through)
        m.addAction(ct)
        q = QAction("Quit", m)
        q.triggered.connect(QApplication.quit)
        m.addAction(q)
        m.exec(e.globalPos())

    def set_alpha(self, a):
        self.bg_alpha = a
        self.save_cfg()
        self.update()

    def set_on_top(self, on):
        self.on_top = bool(on)
        self.apply_native()
        self.save_cfg()

    def enable_click_through(self):
        self.click_through = True
        self.apply_native()


def main():
    global SVC_LABEL, SVC_APP, SVC_URL, SVC_PROC, SVC_TITLE, DEMO
    if sys.platform != "darwin":
        sys.exit("Mac Sysmon only runs on macOS. For Linux see kranmal/linux-sysmon.")
    ap = argparse.ArgumentParser(description="Transparent desktop system monitor (macOS).")
    ap.add_argument("--service-label", default=SVC_LABEL, metavar="LABEL",
                    help="launchd USER agent label to show with a start/stop button")
    ap.add_argument("--service-app", default=SVC_APP, metavar="APP",
                    help='macOS app to start/stop instead of a launchd agent (e.g. "Ollama")')
    ap.add_argument("--service-title", default=SVC_TITLE, help="section title (default SERVICE)")
    ap.add_argument("--service-health-url", default=SVC_URL, metavar="URL",
                    help="optional HTTP URL; 200 means RUNNING, otherwise LOADING")
    ap.add_argument("--service-process", default=SVC_PROC, metavar="NAME",
                    help="optional process name, to show its RAM use")
    ap.add_argument("--demo", action="store_true", help="show fake data (screenshots, testing)")
    args, qt_args = ap.parse_known_args()
    SVC_LABEL, SVC_APP, SVC_TITLE = args.service_label, args.service_app, args.service_title
    SVC_URL, SVC_PROC, DEMO = args.service_health_url, args.service_process, args.demo
    app = QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName("sysmon")
    try:   # no Dock icon, no app-switcher entry
        from AppKit import NSApplication
        NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception:
        pass
    w = Monitor()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
