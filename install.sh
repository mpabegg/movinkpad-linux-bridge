#!/usr/bin/bash
# Installs the bridge for the current user: autostart service plus tray icon.
set -eu
root=$(cd "$(dirname "$0")" && pwd)

(cd "$root/grab" && cargo build --release)
chmod +x "$root/movink-bridge.py" "$root/movink-tray.py"

mkdir -p ~/.config/systemd/user
for name in movink-bridge movink-tray; do
    sed "s|%h/code/wacom|$root|" "$root/desktop/$name.service" > ~/.config/systemd/user/$name.service
done

systemctl --user daemon-reload
systemctl --user enable --now movink-bridge.service movink-tray.service
