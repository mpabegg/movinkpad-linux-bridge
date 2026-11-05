#!/usr/bin/python3
"""Exercises the tray through D-Bus the way Plasma does: left click and menu clicks."""
import json
import subprocess
import time
from pathlib import Path

ADB = Path.home() / "Android/Sdk/platform-tools/adb"
FLAG = Path.home() / ".config/movink-bridge/block_touch"


def run(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def busctl(*args):
    return json.loads(run("busctl", "--user", "--json=short", *args))


def find_tray():
    pid = run("systemctl", "--user", "show", "-p", "MainPID", "--value", "movink-tray.service")
    items = busctl("get-property", "org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
                   "org.kde.StatusNotifierWatcher", "RegisteredStatusNotifierItems")["data"]
    names = {line.split()[0] for line in run("busctl", "--user", "list", "--no-legend").splitlines()
             if line.split()[1] == pid}
    entry = next(item for item in items if item.partition("/")[0] in names)
    service, _, path = entry.partition("/")
    return service, "/" + path


service, item = find_tray()
menu = busctl("get-property", service, item, "org.kde.StatusNotifierItem", "Menu")["data"]
print(f"registered {service} {item} menu={menu}")


def layout():
    data = busctl("call", service, menu, "com.canonical.dbusmenu", "GetLayout", "iias", "0", "--", "-1", "0")["data"]
    rows = []
    for child in data[1][2]:
        node = child["data"]
        rows.append((node[0], node[1].get("label", {}).get("data", ""), node[1].get("toggle-state", {}).get("data")))
    return rows


def click(label):
    node = next(row[0] for row in layout() if row[1] == label)
    busctl_args = ("call", service, menu, "com.canonical.dbusmenu", "Event", "isvu", str(node), "clicked", "i", "0", "0")
    run("busctl", "--user", *busctl_args)


def left_click():
    run("busctl", "--user", "call", service, item, "org.kde.StatusNotifierItem", "Activate", "ii", "0", "0")


def report(step):
    pc = Path("/proc/bus/input/devices").read_text().count("Wacom MovinkPad 11 Pen")
    bridge = run("systemctl", "--user", "is-active", "movink-bridge")
    grabs = run(str(ADB), "shell", 'ps -A -o ARGS | grep "^movink-grab /dev"').split("\n")
    menu_state = [(label, state) for _, label, state in layout() if label]
    print(f"== {step}: pc_device={pc} bridge={bridge} flag={FLAG.exists()} grabs={grabs}\n   {menu_state}")


def display_state():
    outputs = run("kscreen-doctor", "-o")
    usb = run(str(ADB), "shell", "svc usb getFunctions 2>&1")
    device = next((line for line in run("busctl", "--user", "tree", "org.kde.KWin", "--list").splitlines()
                   if "InputDevice/event" in line
                   and "MovinkPad" in run("busctl", "--user", "get-property", "org.kde.KWin", line,
                                          "org.kde.KWin.InputDevice", "name")), None)
    pen = device and run("busctl", "--user", "get-property", "org.kde.KWin", device,
                         "org.kde.KWin.InputDevice", "outputName")
    display = run("systemctl", "--user", "is-active", "movink-display")
    moonlight = run(str(ADB), "shell", "pidof com.limelight") != ""
    print(f"   display={display} monitor={'Virtual-MovinkPad' in outputs} usb=[{usb}] pen={pen} moonlight={moonlight}")


report("start"); display_state()
click("Mode: screenless"); time.sleep(9); report("menu: screenless"); display_state()
click("Mode: extra screen"); time.sleep(15); report("menu: extra screen"); display_state()
left_click(); time.sleep(10); report("left click: pause everything"); display_state()
left_click(); time.sleep(6); report("left click: resume pen only"); display_state()
