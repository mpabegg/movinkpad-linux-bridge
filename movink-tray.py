#!/usr/bin/python3
"""System tray control for the MovinkPad: pen on/off, display mode, pen area and Android touch block."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction, QActionGroup, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

BRIDGE = "movink-bridge.service"
DISPLAY = "movink-display.service"
BLOCK_TOUCH_FILE = Path.home() / ".config/movink-bridge/block_touch"
MATCH_SCREEN_FILE = Path.home() / ".config/movink-bridge/match_screen"
HIDE_AREA_FILE = Path.home() / ".config/movink-bridge/hide_area"
PEN_DEVICE_NAME = "Wacom MovinkPad 11 Pen"
EXTRA_SCREEN_TOOLS = ("krfb-virtualmonitor", "kscreen-doctor", "sunshine")
REFRESH_MS = 2000


def unit_enabled(unit):
    state = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True)
    return state.stdout.strip() in ("active", "activating")


def extra_screen_supported():
    # The virtual monitor, its capture and the pen mapping all go through KWin.
    desktops = os.environ.get("XDG_CURRENT_DESKTOP", "").split(":")
    return "KDE" in desktops and all(shutil.which(tool) for tool in EXTRA_SCREEN_TOOLS)


def pen_connected():
    return PEN_DEVICE_NAME in Path("/proc/bus/input/devices").read_text()


def primary_screen_size():
    size = QApplication.primaryScreen().size()
    return f"{size.width()} {size.height()}\n"


class Tray:
    def __init__(self):
        base = QIcon.fromTheme("input-tablet")
        self.on_icon = base
        self.off_icon = QIcon(base.pixmap(64, QIcon.Mode.Disabled))

        self.menu = QMenu()
        self.status = self.menu.addAction("")
        self.status.setEnabled(False)
        self.menu.addSeparator()
        self.pen_action = QAction("Pen on PC", self.menu, checkable=True)
        self.pen_action.triggered.connect(self.set_bridge)
        self.menu.addAction(self.pen_action)
        self.menu.addSeparator()

        self.modes = QActionGroup(self.menu)
        self.screenless_action = QAction("Mode: screenless", self.modes, checkable=True)
        self.screenless_action.triggered.connect(lambda: self.set_display(False))
        self.display_action = QAction("Mode: extra screen", self.modes, checkable=True)
        self.display_action.triggered.connect(lambda: self.set_display(True))
        if extra_screen_supported():
            self.menu.addActions(self.modes.actions())
            self.menu.addSeparator()

        self.match_action = QAction("Match screen ratio", self.menu, checkable=True)
        self.match_action.triggered.connect(self.set_match_screen)
        self.menu.addAction(self.match_action)
        self.area_action = QAction("Show area on tablet", self.menu, checkable=True)
        self.area_action.triggered.connect(self.set_area_visible)
        self.menu.addAction(self.area_action)
        self.menu.addSeparator()

        self.touch_action = QAction("Block Android touch", self.menu, checkable=True)
        self.touch_action.triggered.connect(self.set_touch_block)
        self.menu.addAction(self.touch_action)

        self.tray = QSystemTrayIcon(self.off_icon)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_activated)
        self.tray.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(REFRESH_MS)
        self.refresh()

    def on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.set_bridge(not self.pen_action.isChecked())

    def systemctl(self, verb, *units):
        # --no-block: both units talk to the tablet over adb and would freeze the menu.
        result = subprocess.run(["systemctl", "--user", "--no-block", verb, *units], capture_output=True, text=True)
        if result.returncode != 0:
            self.tray.showMessage("MovinkPad", result.stderr.strip(), QSystemTrayIcon.MessageIcon.Warning)

    def set_bridge(self, enabled):
        # Paused means the whole tablet goes back to Android, including the extra screen.
        if enabled:
            self.systemctl("start", BRIDGE)
        else:
            self.systemctl("stop", DISPLAY, BRIDGE)
        self.refresh(enabled, enabled and self.display_action.isChecked())

    def set_display(self, enabled):
        self.systemctl("start" if enabled else "stop", DISPLAY)
        self.refresh(enabled or self.pen_action.isChecked(), enabled)

    def set_match_screen(self, enabled):
        if enabled:
            MATCH_SCREEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            MATCH_SCREEN_FILE.write_text(primary_screen_size())
        else:
            MATCH_SCREEN_FILE.unlink(missing_ok=True)
        self.refresh()

    def set_area_visible(self, visible):
        if visible:
            HIDE_AREA_FILE.unlink(missing_ok=True)
        else:
            HIDE_AREA_FILE.parent.mkdir(parents=True, exist_ok=True)
            HIDE_AREA_FILE.touch()
        self.refresh()

    def set_touch_block(self, blocked):
        if blocked:
            BLOCK_TOUCH_FILE.parent.mkdir(parents=True, exist_ok=True)
            BLOCK_TOUCH_FILE.touch()
        else:
            BLOCK_TOUCH_FILE.unlink(missing_ok=True)
        self.refresh()

    def refresh(self, enabled=None, display=None):
        """Re-read real state so changes made outside the tray show up too."""
        enabled = unit_enabled(BRIDGE) if enabled is None else enabled
        display = unit_enabled(DISPLAY) if display is None else display
        blocked = BLOCK_TOUCH_FILE.exists()
        connected = enabled and pen_connected()
        matched = MATCH_SCREEN_FILE.exists()
        if matched and MATCH_SCREEN_FILE.read_text() != primary_screen_size():
            MATCH_SCREEN_FILE.write_text(primary_screen_size())

        if not enabled:
            status = "Paused: pen on Android"
        elif not connected:
            status = "Waiting for tablet on USB"
        elif display:
            status = "Extra screen: pen on PC"
        else:
            status = "Active: pen on PC"
        if blocked and connected:
            status += ", touch blocked"

        self.status.setText(status)
        self.pen_action.setChecked(enabled)
        (self.display_action if display else self.screenless_action).setChecked(True)
        self.match_action.setChecked(matched)
        self.area_action.setChecked(not HIDE_AREA_FILE.exists())
        self.area_action.setEnabled(matched)
        self.touch_action.setChecked(blocked)
        self.tray.setIcon(self.on_icon if connected else self.off_icon)
        self.tray.setToolTip(f"MovinkPad\n{status}")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MovinkPad")
    app.setQuitOnLastWindowClosed(False)
    tray = Tray()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
