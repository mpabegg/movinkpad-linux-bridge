#!/usr/bin/python3
"""System tray control for the MovinkPad bridge: pen on/off and Android touch block."""
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

UNIT = "movink-bridge.service"
BLOCK_TOUCH_FILE = Path.home() / ".config/movink-bridge/block_touch"
PEN_DEVICE_NAME = "Wacom MovinkPad 11 Pen"
REFRESH_MS = 2000


def bridge_enabled():
    state = subprocess.run(["systemctl", "--user", "is-active", UNIT], capture_output=True, text=True)
    return state.stdout.strip() in ("active", "activating")


def pen_connected():
    return PEN_DEVICE_NAME in Path("/proc/bus/input/devices").read_text()


class Tray:
    def __init__(self):
        base = QIcon.fromTheme("input-tablet")
        self.on_icon = base
        self.off_icon = QIcon(base.pixmap(64, QIcon.Mode.Disabled))

        self.menu = QMenu()
        self.status = self.menu.addAction("")
        self.status.setEnabled(False)
        self.menu.addSeparator()
        self.pen_action = QAction("Caneta no PC", self.menu, checkable=True)
        self.pen_action.triggered.connect(self.set_bridge)
        self.menu.addAction(self.pen_action)
        self.touch_action = QAction("Bloquear toque do Android", self.menu, checkable=True)
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

    def set_bridge(self, enabled):
        # --no-block: stopping talks to the tablet over adb and would freeze the menu.
        verb = "start" if enabled else "stop"
        result = subprocess.run(["systemctl", "--user", "--no-block", verb, UNIT], capture_output=True, text=True)
        if result.returncode != 0:
            self.tray.showMessage("MovinkPad", result.stderr.strip(), QSystemTrayIcon.MessageIcon.Warning)
        self.pen_action.setChecked(enabled)
        self.refresh(enabled)

    def set_touch_block(self, blocked):
        if blocked:
            BLOCK_TOUCH_FILE.parent.mkdir(parents=True, exist_ok=True)
            BLOCK_TOUCH_FILE.touch()
        else:
            BLOCK_TOUCH_FILE.unlink(missing_ok=True)
        self.refresh()

    def refresh(self, enabled=None):
        """Re-read real state so changes made outside the tray show up too."""
        enabled = bridge_enabled() if enabled is None else enabled
        blocked = BLOCK_TOUCH_FILE.exists()
        connected = enabled and pen_connected()

        if not enabled:
            status = "Pausado: caneta no Android"
        elif connected:
            status = "Ativo: caneta no PC"
        else:
            status = "Aguardando tablet no USB"
        if blocked and connected:
            status += ", toque bloqueado"

        self.status.setText(status)
        self.pen_action.setChecked(enabled)
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
