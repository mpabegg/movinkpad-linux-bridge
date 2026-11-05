#!/usr/bin/bash
# Installs the bridge for the current user: autostart service, tray icon and the
# on-demand extra-screen service. The extra screen also needs krfb and Sunshine on
# the PC, plus Moonlight on the tablet paired with Sunshine.
set -eu
root=$(cd "$(dirname "$0")" && pwd)

(cd "$root/grab" && cargo build --release)
chmod +x "$root/movink-bridge.py" "$root/movink-display.py" "$root/movink-tray.py"

mkdir -p ~/.config/systemd/user
for name in movink-bridge movink-display movink-tray; do
    sed "s|%h/code/wacom|$root|" "$root/desktop/$name.service" > ~/.config/systemd/user/$name.service
done

# The tablet offers a default route over tethering; the PC must keep its own network.
if ! nmcli -g NAME connection show | grep -qx movink-usb; then
    nmcli connection add type ethernet con-name movink-usb ifname '*' match.driver rndis_host \
        connection.autoconnect yes connection.autoconnect-priority 10 \
        ipv4.method auto ipv4.never-default yes ipv4.ignore-auto-dns yes ipv6.method disabled
fi

systemctl --user daemon-reload
systemctl --user enable --now movink-bridge.service movink-tray.service
systemctl --user restart movink-tray.service
