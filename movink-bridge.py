#!/usr/bin/python3
"""Use a Wacom MovinkPad 11 as a screenless pen tablet on Linux, over USB/ADB."""
import argparse
import fcntl
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRAB_LOCAL = HERE / "grab/target/aarch64-unknown-linux-musl/release/movink-grab"
GRAB_REMOTE = "/data/local/tmp/movink-grab"
OVERLAY_LOCAL = HERE / "overlay/movink-overlay.dex"
OVERLAY_REMOTE = "/data/local/tmp/movink-overlay.dex"
STAY_ON_FILE = Path.home() / ".local/state/movink-bridge/stay_on"
BLOCK_TOUCH_FILE = Path.home() / ".config/movink-bridge/block_touch"
MATCH_SCREEN_FILE = Path.home() / ".config/movink-bridge/match_screen"
HIDE_AREA_FILE = Path.home() / ".config/movink-bridge/hide_area"
DISPLAY_UNIT = "movink-display.service"

MODEL = "DTHA116"
DEVICE_POLL_SECONDS = 2
WATCH_POLL_SECONDS = 0.5
ROTATION_POLL_TICKS = 4
STAY_ON_USB = 2

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y, ABS_PRESSURE, ABS_TILT_X, ABS_TILT_Y = 0x00, 0x01, 0x18, 0x1A, 0x1B
BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH, BTN_STYLUS, BTN_STYLUS2 = 0x140, 0x141, 0x14A, 0x14B, 0x14C
KEYS = (BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH, BTN_STYLUS, BTN_STYLUS2)
INPUT_PROP_POINTER = 0
BUS_USB = 3

UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
UI_DEV_SETUP, UI_ABS_SETUP = 0x405C5503, 0x401C5504
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT, UI_SET_PROPBIT = 0x40045564, 0x40045565, 0x40045567, 0x4004556E

# Digitizer limits from `getevent -pl` on the MovinkPad 11 (portrait axes).
WIDTH, HEIGHT, PRESSURE_MAX, TILT_MAX = 15928, 24334, 8191, 9000
SCREEN_WIDTH, SCREEN_HEIGHT = 1440, 2200
RES_MM, RES_TILT = 100, 5730

EVENT = struct.Struct("<qqHHi")


def rotate(code, value, rotation):
    """Map a portrait-axis ABS event to the axes of the given Android rotation."""
    if rotation == 1:
        return {
            ABS_X: (ABS_Y, WIDTH - value),
            ABS_Y: (ABS_X, value),
            ABS_TILT_X: (ABS_TILT_Y, -value),
            ABS_TILT_Y: (ABS_TILT_X, value),
        }.get(code, (code, value))
    if rotation == 2:
        return {
            ABS_X: (ABS_X, WIDTH - value),
            ABS_Y: (ABS_Y, HEIGHT - value),
            ABS_TILT_X: (ABS_TILT_X, -value),
            ABS_TILT_Y: (ABS_TILT_Y, -value),
        }.get(code, (code, value))
    if rotation == 3:
        return {
            ABS_X: (ABS_Y, value),
            ABS_Y: (ABS_X, HEIGHT - value),
            ABS_TILT_X: (ABS_TILT_Y, value),
            ABS_TILT_Y: (ABS_TILT_X, -value),
        }.get(code, (code, value))
    return code, value


def wanted_screen():
    """Monitor size the pen area should match; None keeps the whole digitizer."""
    active = subprocess.run(["systemctl", "--user", "--quiet", "is-active", DISPLAY_UNIT]).returncode == 0
    if active:
        return None
    try:
        width, height = MATCH_SCREEN_FILE.read_text().split()
        return int(width), int(height)
    except (OSError, ValueError):
        return None


def fit_area(rotation, screen):
    """Largest centered (left, top, right, bottom) with the screen's ratio, in rotated digitizer axes."""
    width, height = (HEIGHT, WIDTH) if rotation % 2 else (WIDTH, HEIGHT)
    if screen is None:
        return 0, 0, width, height
    area_width = min(width, height * screen[0] // screen[1])
    area_height = min(height, width * screen[1] // screen[0])
    left, top = (width - area_width) // 2, (height - area_height) // 2
    return left, top, left + area_width, top + area_height


class VirtualPen:
    def __init__(self, area):
        self.down = set()
        self.create(area)

    def create(self, area):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        self.area = left, top, right, bottom = area
        for bit in (EV_SYN, EV_KEY, EV_ABS):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, bit)
        for key in KEYS:
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, key)
        fcntl.ioctl(self.fd, UI_SET_PROPBIT, INPUT_PROP_POINTER)
        axes = (
            (ABS_X, left, right, RES_MM),
            (ABS_Y, top, bottom, RES_MM),
            (ABS_PRESSURE, 0, PRESSURE_MAX, 0),
            (ABS_TILT_X, -TILT_MAX, TILT_MAX, RES_TILT),
            (ABS_TILT_Y, -TILT_MAX, TILT_MAX, RES_TILT),
        )
        for code, low, high, res in axes:
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
            fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack("<HxxIiiiii", code, 0, low, high, 0, 0, res))
        name = b"Wacom MovinkPad 11 Pen"
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("<HHHH80sI", BUS_USB, 0x056A, 0x040C, 1, name, 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)

    def emit(self, ev_type, code, value):
        os.write(self.fd, EVENT.pack(0, 0, ev_type, code, value))

    def axis(self, code, value):
        """Past the area the pen slides along its edge, as on a tablet with a smaller active area."""
        left, top, right, bottom = self.area
        if code == ABS_X:
            value = max(left, min(value, right))
        elif code == ABS_Y:
            value = max(top, min(value, bottom))
        self.emit(EV_ABS, code, value)

    def key(self, code, value):
        (self.down.add if value else self.down.discard)(code)
        self.emit(EV_KEY, code, value)

    def release_all(self):
        """Never leave a stroke stuck down if the stream dies mid-contact."""
        if not self.down:
            return
        self.emit(EV_ABS, ABS_PRESSURE, 0)
        for key in sorted(self.down, reverse=True):
            self.emit(EV_KEY, key, 0)
        self.down.clear()
        self.emit(EV_SYN, 0, 0)

    def close(self):
        self.release_all()
        fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        os.close(self.fd)

    def rebuild(self, area):
        """Axis ranges are fixed at creation, so a new area needs a new device."""
        self.close()
        self.create(area)


class Adb:
    def __init__(self, path, serial):
        self.path = path
        self.wanted_serial = serial
        self.serial = serial

    @property
    def base(self):
        return [self.path, "-s", self.serial]

    def find_device(self):
        """Pick the attached MovinkPad; USB transport only, other Android devices are ignored."""
        result = subprocess.run([self.path, "devices", "-l"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            match = re.match(rf"(\S+)\s+device\s+usb:.*\bmodel:{MODEL}\b", line)
            if match and self.wanted_serial in (None, match.group(1)):
                self.serial = match.group(1)
                return True
        return False

    def shell(self, command, timeout=15):
        result = subprocess.run(self.base + ["shell", command], capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout

    def find_input(self, marker):
        _, listing = self.shell("getevent -pl")
        for block in re.split(r"(?=^add device)", listing, flags=re.M):
            if marker in block:
                match = re.search(r"(/dev/input/event\d+)", block)
                if match:
                    return match.group(1)
        raise RuntimeError(f"no input device with {marker} in getevent output")

    def rotation(self):
        _, listing = self.shell("dumpsys input | grep -m1 'Viewport INTERNAL'")
        match = re.search(r"orientation=(\d)", listing)
        return int(match.group(1)) if match else None

    def stream_command(self, node):
        """Prefer the exclusive-grab helper; fall back to a plain read if it can't grab."""
        self.shell(f"pkill -f {GRAB_REMOTE}")
        if GRAB_LOCAL.exists():
            subprocess.run(self.base + ["push", str(GRAB_LOCAL), GRAB_REMOTE], capture_output=True, timeout=30)
            self.shell(f"chmod 755 {GRAB_REMOTE}")
            code, _ = self.shell(f"{GRAB_REMOTE} --check {node}")
            if code == 0:
                return self.base + ["exec-out", GRAB_REMOTE, node], True
        return self.base + ["exec-out", "cat", node], False

    def block_touch(self, node):
        """Grab the touchscreen and discard its events, so Android stops seeing fingers."""
        command = self.base + ["exec-out", GRAB_REMOTE, node]
        return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def unblock_touch(self, node):
        # The helper only notices a dead pipe on its next event, which would eat one touch.
        self.shell(f"pkill -f '{GRAB_REMOTE} {node}'")

    def start_overlay(self):
        if not OVERLAY_LOCAL.exists():
            return None
        subprocess.run(self.base + ["push", str(OVERLAY_LOCAL), OVERLAY_REMOTE], capture_output=True, timeout=30)
        command = self.base + ["shell", f"CLASSPATH={OVERLAY_REMOTE} app_process / MovinkOverlay"]
        return subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True
        )

    def stay_awake(self):
        """Keep the digitizer powered on USB; the prior value survives unplugs on disk."""
        _, value = self.shell("settings get global stay_on_while_plugged_in")
        value = value.strip()
        if not value.isdigit() or int(value) & STAY_ON_USB:
            return
        STAY_ON_FILE.parent.mkdir(parents=True, exist_ok=True)
        STAY_ON_FILE.write_text(value)
        self.shell(f"settings put global stay_on_while_plugged_in {int(value) | STAY_ON_USB}")

    def release(self):
        """Hand the tablet back: pen to Android, stay-awake setting to what it was."""
        if not self.find_device():
            return
        self.shell(f"pkill -f {GRAB_REMOTE}")
        if STAY_ON_FILE.exists():
            self.shell(f"settings put global stay_on_while_plugged_in {STAY_ON_FILE.read_text()}")
            STAY_ON_FILE.unlink()


class AreaOverlay:
    """Marks the pen area on the tablet's screen; the layer goes away with this adb shell."""

    def __init__(self, adb):
        self.adb = adb
        self.child = None
        self.shown = None

    def sync(self, rotation, area):
        wanted = area and (rotation % 2, area)
        if wanted == self.shown:
            return
        if self.child is None:
            self.child = self.adb.start_overlay()
        if self.child is None:
            return
        self.child.stdin.write(self.command(rotation, area))
        self.child.stdin.flush()
        self.shown = wanted

    @staticmethod
    def command(rotation, area):
        if area is None:
            return "hide\n"
        width, height = (SCREEN_HEIGHT, SCREEN_WIDTH) if rotation % 2 else (SCREEN_WIDTH, SCREEN_HEIGHT)
        _, _, full_width, full_height = fit_area(rotation, None)
        left, top, right, bottom = area
        pixels = (left * width // full_width, top * height // full_height,
                  right * width // full_width, bottom * height // full_height)
        return " ".join(map(str, (width, height, *pixels))) + "\n"

    def stop(self):
        if self.child is None:
            return
        self.child.terminate()
        self.child.wait()
        self.child = None
        self.shown = None


class TabletWatcher(threading.Thread):
    """Runs off the event path: a blocking adb call in pump() would stall the pen."""

    def __init__(self, adb, layout, follow_rotation, touch_node):
        super().__init__(daemon=True)
        self.adb = adb
        self.layout = layout
        self.follow_rotation = follow_rotation
        self.touch_node = touch_node
        self.touch_block = None
        self.overlay = AreaOverlay(adb)
        self.stopped = threading.Event()

    def run(self):
        ticks = 0
        while not self.stopped.wait(WATCH_POLL_SECONDS):
            ticks += 1
            try:
                self.sync_touch_block()
                if ticks % ROTATION_POLL_TICKS == 0:
                    self.sync_layout()
            except (subprocess.SubprocessError, OSError) as err:
                print(f"watch failed: {err}", flush=True)

    def sync_layout(self):
        rotation = self.adb.rotation() if self.follow_rotation else None
        rotation = self.layout[0] if rotation is None else rotation
        screen = wanted_screen()
        self.layout = rotation, screen
        visible = screen is not None and not HIDE_AREA_FILE.exists()
        self.overlay.sync(rotation, fit_area(rotation, screen) if visible else None)

    def sync_touch_block(self):
        wanted = self.touch_node is not None and BLOCK_TOUCH_FILE.exists()
        running = self.touch_block is not None and self.touch_block.poll() is None
        if wanted and not running:
            self.touch_block = self.adb.block_touch(self.touch_node)
            print("touch blocked", flush=True)
        elif running and not wanted:
            self.release_touch()
            print("touch released", flush=True)

    def release_touch(self):
        if self.touch_block is None:
            return
        self.touch_block.terminate()
        self.touch_block.wait()
        self.touch_block = None
        self.adb.unblock_touch(self.touch_node)


def pump(fd, pen, watcher):
    size = EVENT.size
    pending = b""
    layout = watcher.layout
    rotation = layout[0]
    while True:
        ready, _, _ = select.select([fd], [], [], 0.5)
        wanted = watcher.layout
        if wanted != layout and not pen.down:
            area = fit_area(*wanted)
            if area != pen.area:
                pen.rebuild(area)
            layout = wanted
            rotation = layout[0]
            print(f"rotation={rotation} area={area}", flush=True)
        if not ready:
            continue
        chunk = os.read(fd, size * 64)
        if not chunk:
            return
        pending += chunk
        usable = len(pending) - len(pending) % size
        for _, _, ev_type, code, value in EVENT.iter_unpack(pending[:usable]):
            if ev_type == EV_ABS and code in (ABS_X, ABS_Y, ABS_PRESSURE, ABS_TILT_X, ABS_TILT_Y):
                pen.axis(*rotate(code, value, rotation))
            elif ev_type == EV_KEY and code in KEYS:
                pen.key(code, value)
            elif ev_type == EV_SYN and code == 0:
                pen.emit(EV_SYN, 0, 0)
        pending = pending[usable:]


def run_connection(adb, fixed_rotation):
    """Serve one USB connection; returns when the stream ends (unplug, adb restart)."""
    rotation = adb.rotation() if fixed_rotation is None else fixed_rotation
    if rotation is None:
        raise RuntimeError("display rotation not available")
    node = adb.find_input("ABS_TILT_X")
    touch_node = adb.find_input("ABS_MT_POSITION_X")
    adb.stay_awake()
    command, grabbed = adb.stream_command(node)
    layout = rotation, wanted_screen()
    pen = VirtualPen(fit_area(*layout))
    watcher = TabletWatcher(adb, layout, fixed_rotation is None, touch_node if grabbed else None)
    watcher.start()
    print(f"streaming {node} rotation={rotation} grab={grabbed}", flush=True)
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    try:
        pump(child.stdout.fileno(), pen, watcher)
    finally:
        watcher.stopped.set()
        watcher.join()
        watcher.release_touch()
        watcher.overlay.stop()
        child.terminate()
        child.wait()
        pen.close()


def find_adb():
    candidates = (shutil.which("adb"), Path.home() / "Android/Sdk/platform-tools/adb")
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    sys.exit("adb not found")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", help="adb serial, when several MovinkPads are attached")
    parser.add_argument("--rotation", type=int, choices=range(4), help="pin Android rotation (0-3)")
    args = parser.parse_args()

    adb = Adb(find_adb(), args.serial)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"waiting for {MODEL} on USB", flush=True)
    try:
        while True:
            if not adb.find_device():
                time.sleep(DEVICE_POLL_SECONDS)
                continue
            try:
                run_connection(adb, args.rotation)
                print("stream ended", flush=True)
            except (RuntimeError, subprocess.SubprocessError) as err:
                print(f"connection failed: {err}", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            adb.release()
        except (subprocess.SubprocessError, OSError) as err:
            print(f"release failed: {err}", flush=True)


if __name__ == "__main__":
    main()
