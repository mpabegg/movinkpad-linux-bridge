# MovinkPad Linux Bridge

Use a Wacom MovinkPad 11 from a Linux PC over USB, either as a screenless pen
tablet or as an extra pen display. Wacom's Instant Pen Display app only exists
for Windows and macOS.

Not affiliated with or endorsed by Wacom. "Wacom" and "MovinkPad" are trademarks
of Wacom Co., Ltd.

No root on the tablet and no custom APK. The pen keeps pressure, tilt and both
side buttons.

| Mode | Image on the tablet | Pen |
| --- | --- | --- |
| Screenless | None | Controls the PC |
| Extra screen | A virtual monitor, streamed over USB | Mapped to that monitor |

## Support

Developed and tested on one setup: MovinkPad 11, Fedora 44, KDE Plasma 6 on
Wayland, AMD GPU.

| Setup | Status |
| --- | --- |
| MovinkPad 11, Fedora KDE, AMD GPU | Tested |
| Other distro with KDE Plasma | Expected to work, untested |
| Intel or NVIDIA GPU | Screenless unaffected; extra screen depends on Sunshine's encoder, untested |
| GNOME and other desktops | Screenless expected to work, tray untested; extra screen needs KWin and krfb |
| MovinkPad Pro 14 | Not supported yet: model and digitizer limits are hardcoded |
| Other Android tablets | Not supported |

Have a Pro 14? The output of `adb shell getevent -pl` is what support needs.

## Install

On the tablet, enable **Developer options** (tap **Build number** 7 times) and
then **USB debugging**. Connect it by USB and accept the prompt.

On the PC:

```sh
git clone https://github.com/mpabegg/movinkpad-linux-bridge.git
cd movinkpad-linux-bridge
./install.sh --check   # lists missing tools
./install.sh
```

Requires `adb`, [rustup](https://rustup.rs) and PyQt6. The installer builds a
small ARM64 helper, asks for your password once to add a udev rule for
`/dev/uinput`, and enables two user services. A JDK and the Android SDK
build-tools are optional: without them, the pen area is not drawn on the tablet.

That is all for screenless mode: plug in the tablet and draw.

## Extra screen setup

1. Install `krfb` and [Sunshine](https://github.com/LizardByte/Sunshine)
   2026.516 or newer, which added KWin capture.
2. Make sure Sunshine has a hardware encoder. On Fedora with an AMD GPU, that
   means `mesa-va-drivers-freeworld` from RPM Fusion; without it Sunshine falls
   back to the CPU.
3. Set the Sunshine login: `sunshine --creds <user> <password>`.
4. Install [Moonlight](https://github.com/moonlight-stream/moonlight-android) on
   the tablet.
5. Choose **Mode: extra screen** in the tray.
6. Find the PC's USB address: `journalctl --user -u movink-display -n 5`
   (`display ready on <address>`).
7. In Moonlight, tap **+** and add that address. Tap the new PC to get a PIN.
8. On the PC, open `https://<address>:47990`, accept the self-signed certificate
   and enter the PIN.
9. In Moonlight settings, choose the native resolution, 90 FPS, **Prefer lowest
   latency** and a bitrate around 60 Mbps.

If your firewall blocks it, allow TCP 47984-47990 and 48010 plus UDP 47998-48000
on the USB interface. An existing Sunshine setup stays intact: this mode uses
`~/.config/movink-bridge/sunshine.conf`, but pairing is shared with your normal
Sunshine. Do not run both at the same time.

After pairing, the mode opens the stream by itself. If it cannot, Moonlight stops
at the PC list; tap the PC and then **Desktop**. Rearrange the monitor in KDE's
**Display Configuration**.

## Use

The tray icon starts at login.

| Menu item | Meaning |
| --- | --- |
| Left click, or **Pen on PC** | Pen on the PC, or pause: everything returns to Android |
| **Mode: screenless** | Screenless mode |
| **Mode: extra screen** | Extra screen mode |
| **Block Android touch** | Fingers do nothing on the tablet, in Moonlight too |
| **Match screen ratio** | Crops the pen area to the ratio of the PC's primary monitor |
| **Show area on tablet** | Dims the tablet outside the pen area and outlines it |

The icon is colored while the pen is on the PC. Pressure curves and pen buttons
are configured in KDE's **Drawing Tablet** settings.

**Match screen ratio** keeps circles round on a monitor whose shape differs from
the tablet's. On a 3440x1440 monitor, the area is 243 x 102 mm, centered. Past
the area, the pen slides along its edge. The ratio applies in screenless mode
only: in extra screen mode, the pen always covers the whole tablet.

## Limitations

- While the pen is on the PC, it does not work on Android. Pause to get it back.
- Extra screen mode starts only from the tray, never at login.
- Sunshine audio is disabled, so PC sound stays on the PC.
- USB debugging must stay on.

## How it works

The tablet's digitizer is readable from an `adb shell` without root.
`movink-bridge.py` streams its evdev events to the PC and recreates the pen with
`uinput`. `grab/` is an ARM64 helper that holds an exclusive grab, so Android does
not also react to the pen. The pen area is the axis range of that `uinput` device.
`overlay/` is a dex run with `app_process` from the same shell, which draws a
SurfaceFlinger layer with no input window, so touch passes through it.

`movink-display.py` switches the tablet to USB tethering, creates a monitor with
`krfb-virtualmonitor` and streams it with Sunshine to Moonlight. Sunshine binds to
the USB address only, and krfb runs in an empty network namespace, so neither is
exposed on your LAN.

## Uninstall

```sh
systemctl --user disable --now movink-display movink-tray movink-bridge
rm ~/.config/systemd/user/movink-{bridge,display,tray}.service
nmcli connection delete movink-usb
sudo rm /etc/udev/rules.d/70-movink-uinput.rules
adb shell rm /data/local/tmp/movink-grab /data/local/tmp/movink-overlay.dex
```

## License

[MIT](LICENSE)
