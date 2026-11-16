#!/usr/bin/env python3
"""Smoke tests: render the overlay with demo data, with and without the optional
service section, and check the real sensors on this Mac don't crash it.

Run:  .venv/bin/python tests/run_tests.py
"""
import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sysmon as m  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

failed = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        failed.append(name)


def settle(w, n=3):
    for _ in range(n):
        w.sample()
        time.sleep(0.1)
        for _ in range(3):
            app.processEvents()


app = QApplication([])
out = tempfile.mkdtemp(prefix="sysmon-test-")

check("human()", m.human(0).strip() == "0.0 B/s" and "MB/s" in m.human(5 * 2**20))
check("fmt_size()", m.fmt_size(600 * 2**20) == "600M" and m.fmt_size(500 * 2**30) == "500G"
      and m.fmt_size(2 * 2**40) == "2.00T")

# demo mode, service section off
m.DEMO = True
w = m.Monitor()
w.show()
settle(w)
h_off = w.height()
img = w.grab()
img.save(f"{out}/off.png")
check("renders (demo, no service)", not img.isNull() and w.width() == m.W)
check("demo disks listed", len(w.disks) == 2)
check("demo never touches saved config", w.cfg == {})

# start position: docked right by default, left on request, saved spot with "remember"
scr = app.primaryScreen().availableGeometry()
w.cfg = {"x": scr.left() + 50, "y": scr.top() + 120}
check("opens on the right edge", w.start_pos().x() == scr.right() + 1 - m.W - m.EDGE
      and w.start_pos().y() == scr.top() + 120, str(w.start_pos()))
m.POSITION = "left"
check("opens on the left edge", w.start_pos().x() == scr.left() + m.EDGE)
m.POSITION = "remember"
check("remembers position", (w.start_pos().x(), w.start_pos().y()) == (scr.left() + 50, scr.top() + 120))
m.POSITION, w.cfg = "right", {}

# demo mode, service section on
m.SVC_LABEL, m.SVC_TITLE = "com.example.demo", "LOCAL LLM"
w2 = m.Monitor()
w2.show()
settle(w2)
check("service section adds height", w2.height() > h_off, f"{h_off} -> {w2.height()}")
check("service state shown", w2.svc_state == "running")
w2.grab().save(f"{out}/on.png")

# app mode: a toggle for an app that isn't running reads as off and takes a click
m.SVC_LABEL, m.SVC_APP = "", "NoSuchAppForSysmonTest"
w4 = m.Monitor()
check("app toggle enabled", w4.svc_enabled and w4.app_procs() == [])
m.SVC_APP = ""

# real sensors on this machine: must not raise
m.DEMO, m.SVC_LABEL = False, ""
w3 = m.Monitor()
w3.show()
settle(w3)
s = w3.s
check("real cpu/ram", 0 <= s["cpu"] <= 100 and s["ram_total"] > 0)
check("real disks", len(w3.disks) >= 1 and w3.disks[0]["parts"][0]["pct"] is not None)
check("real gpu", s.get("gpu") is None or 0 <= s["gpu"] <= 100, str(s.get("gpu")))
check("real cpu temp", s.get("cpu_t") is None or 0 < s["cpu_t"] < 130, str(s.get("cpu_t")))
check("real render", not w3.grab().isNull())
check("click guard (no service)", not w3.svc_enabled)

print(f"\nimages in {out}")
print("ALL PASSED" if not failed else "FAILED: " + ", ".join(failed))
sys.exit(1 if failed else 0)
