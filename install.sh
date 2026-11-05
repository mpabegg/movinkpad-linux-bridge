#!/usr/bin/bash
# Installs the bridge for the current user: autostart service, tray icon and the
# on-demand extra-screen service. `./install.sh --check` only reports missing tools.
set -eu
root=$(cd "$(dirname "$0")" && pwd)
rule=/etc/udev/rules.d/70-movink-uinput.rules
missing=0

has_adb() { command -v adb >/dev/null || [ -x ~/Android/Sdk/platform-tools/adb ]; }
has_pyqt() { /usr/bin/python3 -c 'import PyQt6.QtWidgets' 2>/dev/null; }

need() {
    if ! eval "$1"; then
        echo "missing: $2"
        missing=1
    fi
}

want() {
    if ! command -v "$1" >/dev/null; then
        echo "extra screen will not work without $1 ($2)"
    fi
}

need has_adb "adb (android-tools package, or the Android SDK platform-tools)"
need has_pyqt "PyQt6 for /usr/bin/python3 (python3-pyqt6-base on Fedora, python3-pyqt6 on Debian/Ubuntu)"
need "command -v rustup >/dev/null" "rustup (https://rustup.rs): distro Rust packages lack the ARM64 target and rust-lld"
need "command -v systemctl >/dev/null" "systemd"
want krfb-virtualmonitor "krfb package"
want sunshine "https://github.com/LizardByte/Sunshine"
want kscreen-doctor "libkscreen tools, part of KDE Plasma"
want busctl "systemd"
want nmcli "NetworkManager"

[ "$missing" = 0 ] || exit 1
[ "${1:-}" != "--check" ] || exit 0

rustup target add aarch64-unknown-linux-musl
(cd "$root/grab" && cargo build --release)
chmod +x "$root/movink-bridge.py" "$root/movink-display.py" "$root/movink-tray.py"

if ! cmp -s "$root/desktop/70-movink-uinput.rules" "$rule"; then
    pkexec sh -c 'install -m 644 "$1" "$2" && udevadm control --reload && udevadm trigger --name-match=uinput' \
        sh "$root/desktop/70-movink-uinput.rules" "$rule"
fi

mkdir -p ~/.config/systemd/user
for name in movink-bridge movink-display movink-tray; do
    sed "s|%h/code/wacom|$root|" "$root/desktop/$name.service" > ~/.config/systemd/user/$name.service
done

# The tablet offers a default route over tethering; the PC must keep its own network.
if command -v nmcli >/dev/null && ! nmcli -g NAME connection show | grep -qx movink-usb; then
    nmcli connection add type ethernet con-name movink-usb ifname '*' match.driver rndis_host \
        connection.autoconnect yes connection.autoconnect-priority 10 \
        ipv4.method auto ipv4.never-default yes ipv4.ignore-auto-dns yes ipv6.method disabled
fi

systemctl --user daemon-reload
systemctl --user enable --now movink-bridge.service movink-tray.service
systemctl --user restart movink-tray.service
