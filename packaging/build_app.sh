#!/bin/bash
# Builds Block1Exam.app: a real double-clickable macOS app, no third-party
# packaging tool required (osacompile ships with macOS). Rebuild + redistribute
# this only when launch.sh's own logic changes — the app's actual code updates
# itself on every launch via git pull, independent of this bundle.
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="Block1Exam.app"
rm -rf "$APP_NAME"

osacompile -o "$APP_NAME" launcher.applescript

mkdir -p "$APP_NAME/Contents/Resources"
cp launch.sh "$APP_NAME/Contents/Resources/launch.sh"
chmod +x "$APP_NAME/Contents/Resources/launch.sh"

# osacompile ad-hoc-signs the bundle; adding launch.sh above invalidates that
# signature (the seal no longer matches the bundle's actual contents). Left
# unsigned-again, a downloaded+quarantined copy fails Gatekeeper's integrity
# check and macOS reports the app as "damaged" — not the expected "unidentified
# developer" prompt. Re-sign after every change to the bundle contents.
codesign --force --deep --sign - "$APP_NAME"
codesign --verify --deep --strict "$APP_NAME"

echo "Built packaging/$APP_NAME"
echo "First launch will show an 'unidentified developer' Gatekeeper prompt —"
echo "right-click the app > Open once to bypass it permanently for this app."
