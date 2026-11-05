#!/usr/bin/bash
# Captures libinput's view of the virtual pen. Run as root: reads /dev/input directly.
set -u
owner=$1 seconds=${2:-45}
out=$(cd "$(dirname "$0")" && pwd)/verify
mkdir -p "$out"
node=$(grep -A9 'Wacom MovinkPad 11 Pen' /proc/bus/input/devices | grep -oE 'event[0-9]+')
libinput list-devices > "$out/list-devices.txt" 2>&1
timeout -s INT "$seconds" stdbuf -oL libinput debug-events --device "/dev/input/$node" > "$out/debug-events.txt" 2>&1
chown -R "$owner" "$out"
