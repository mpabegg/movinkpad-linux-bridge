#!/usr/bin/python3
"""Extra-screen mode: a virtual monitor streamed to the MovinkPad over USB tethering."""
import json
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

MODEL = "DTHA116"
OUTPUT = "Virtual-MovinkPad"
RESOLUTION = "2200x1440"
PEN_DEVICE_NAME = "Wacom MovinkPad 11 Pen"
USB_NET_DRIVER = "rndis_host"
SUNSHINE_CONF = Path.home() / ".config/sunshine/sunshine.conf"
SUNSHINE_STATE = Path.home() / ".config/sunshine/sunshine_state.json"
SUNSHINE_PORT = 47989
# Sunshine derives app ids from name and image, so the stock Desktop app always has this one.
DESKTOP_APP_ID = 881448767
POLL_SECONDS = 2
STOP_SECONDS = 8

KWIN = ("org.kde.KWin",)
KWIN_DEVICE = "org.kde.KWin.InputDevice"


def wait_for(probe, seconds, what):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = probe()
        if result:
            return result
        time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for {what}")


class Adb:
    def __init__(self, path):
        self.path = path
        self.serial = None

    def find_device(self):
        """USB transport only: the mode must never run over Wi-Fi adb."""
        result = subprocess.run([self.path, "devices", "-l"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            match = re.match(rf"(\S+)\s+device\s+usb:.*\bmodel:{MODEL}\b", line)
            if match:
                self.serial = match.group(1)
                return True
        return False

    def shell(self, command):
        args = [self.path, "-s", self.serial, "shell", command]
        return subprocess.run(args, capture_output=True, text=True, timeout=15).stdout.strip()

    def set_usb_functions(self, functions=""):
        # svc reports the current functions on stderr.
        if self.shell("svc usb getFunctions 2>&1") != functions:
            self.shell(f"svc usb setFunctions {functions}")

    def open_stream(self):
        host = json.loads(SUNSHINE_STATE.read_text())["root"]["uniqueid"]
        self.shell("wm dismiss-keyguard")
        self.shell(
            "am start -n com.limelight/.ShortcutTrampoline"
            f" --es UUID {host} --es Name PC --es AppId {DESKTOP_APP_ID} --es AppName Desktop"
        )

    def close_stream(self):
        self.shell("am force-stop com.limelight")


def usb_address():
    """Address of this PC on the tethering link; the interface name depends on the USB port."""
    for net in Path("/sys/class/net").iterdir():
        driver = net / "device/driver"
        if driver.exists() and driver.resolve().name == USB_NET_DRIVER:
            result = subprocess.run(["ip", "-j", "-4", "addr", "show", "dev", net.name], capture_output=True, text=True)
            for iface in json.loads(result.stdout):
                for address in iface.get("addr_info", []):
                    return address["local"]
    return None


def output_exists():
    result = subprocess.run(["kscreen-doctor", "-o"], capture_output=True, text=True)
    return OUTPUT in result.stdout


def start_monitor():
    # krfb has no bind option and would listen on every interface, so give it no network at all.
    # Sunshine captures the output through KWin; the VNC side is never used.
    command = [
        "unshare", "--user", "--map-current-user", "--net",
        "krfb-virtualmonitor", "--name", OUTPUT.removeprefix("Virtual-"), "--resolution", RESOLUTION,
        "--scale", "1", "--port", "15900", "--password", secrets.token_urlsafe(12),
    ]
    monitor = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_for(output_exists, 15, "virtual monitor")
    return monitor


def write_sunshine_conf(address):
    SUNSHINE_CONF.parent.mkdir(parents=True, exist_ok=True)
    SUNSHINE_CONF.write_text(
        "sunshine_name = PC\n"
        "capture = kwin\n"
        f"output_name = {OUTPUT}\n"
        f"bind_address = {address}\n"
        "origin_web_ui_allowed = lan\n"
        "upnp = disabled\n"
        "stream_audio = disabled\n"
        "system_tray = disabled\n"
    )


def port_open(address):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((address, SUNSHINE_PORT)) == 0


def start_sunshine(address):
    write_sunshine_conf(address)
    sunshine = subprocess.Popen(["sunshine"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_for(lambda: port_open(address), 30, "Sunshine")
    return sunshine


def stop(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(STOP_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


class PenMapping:
    """Keeps the pen on the virtual output; the bridge recreates the device on reconnect."""

    def __init__(self):
        self.device = None
        self.previous = None

    def busctl(self, *args):
        result = subprocess.run(["busctl", "--user", *args], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None

    def get(self, device, prop):
        value = self.busctl("get-property", *KWIN, device, KWIN_DEVICE, prop)
        return value.split(" ", 1)[1].strip('"') if value else None

    def set_output(self, device, output):
        self.busctl("set-property", *KWIN, device, KWIN_DEVICE, "outputName", "s", output)

    def find_device(self):
        listing = self.busctl("tree", *KWIN, "--list") or ""
        for device in re.findall(r"/org/kde/KWin/InputDevice/event\d+", listing):
            if self.get(device, "name") == PEN_DEVICE_NAME:
                return device
        return None

    def apply(self):
        current = self.get(self.device, "outputName") if self.device else None
        if current is None:
            self.device = self.find_device()
            current = self.get(self.device, "outputName") if self.device else None
        if current is None or current == OUTPUT:
            return
        if self.previous is None:
            self.previous = current
        self.set_output(self.device, OUTPUT)

    def restore(self):
        device = self.find_device()
        if device and self.previous is not None:
            self.set_output(device, self.previous)
        self.previous = None


def run_session(adb):
    """Serve one USB connection; returns when the tablet leaves or a component dies."""
    pen = PenMapping()
    monitor = sunshine = None
    try:
        adb.set_usb_functions("rndis")
        wait_for(adb.find_device, 15, "adb after USB switch")
        address = wait_for(usb_address, 20, "USB tethering address")
        monitor = start_monitor()
        sunshine = start_sunshine(address)
        adb.open_stream()
        print(f"display ready on {address}", flush=True)
        while adb.find_device() and usb_address() == address and monitor.poll() is None and sunshine.poll() is None:
            pen.apply()
            time.sleep(POLL_SECONDS)
        print("session ended", flush=True)
    finally:
        stop(sunshine)
        stop(monitor)
        pen.restore()


def release(adb):
    """Hand the tablet back as it was: no stream, no tethering."""
    if adb.find_device():
        adb.close_stream()
        adb.set_usb_functions()


def find_adb():
    candidates = (shutil.which("adb"), Path.home() / "Android/Sdk/platform-tools/adb")
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    sys.exit("adb not found")


def main():
    adb = Adb(find_adb())
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"waiting for {MODEL} on USB", flush=True)
    try:
        while True:
            if not adb.find_device():
                time.sleep(POLL_SECONDS)
                continue
            try:
                run_session(adb)
            except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as err:
                print(f"session failed: {err}", flush=True)
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            release(adb)
        except (subprocess.SubprocessError, OSError) as err:
            print(f"release failed: {err}", flush=True)


if __name__ == "__main__":
    main()
