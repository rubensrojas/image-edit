#!/bin/sh
set -eu

ARCH="$(uname -m)"
APP="dist/SRE Image Editor.app"
OUTPUT="release/SRE-Image-Editor-macOS-${ARCH}.dmg"

test -d "$APP"
mkdir -p release build/dmg
rm -rf build/dmg/*
cp -R "$APP" build/dmg/
ln -s /Applications build/dmg/Applications
hdiutil create -volname "SRE Image Editor" -srcfolder build/dmg -ov -format UDZO "$OUTPUT"
