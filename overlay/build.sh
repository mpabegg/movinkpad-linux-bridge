#!/bin/sh
# Builds movink-overlay.dex from the Android SDK; no APK and nothing installed on the tablet.
set -eu

cd "$(dirname "$0")"
sdk=${ANDROID_HOME:-$HOME/Android/Sdk}
platform=$(ls -d "$sdk"/platforms/android-* | sort -V | tail -n 1)
tools=$(ls -d "$sdk"/build-tools/* | sort -V | tail -n 1)

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
javac --release 11 -nowarn -classpath "$platform/android.jar" -d "$work" src/MovinkOverlay.java
"$tools/d8" --release --min-api 29 --lib "$platform/android.jar" --output "$work" "$work"/*.class
cp "$work/classes.dex" movink-overlay.dex
