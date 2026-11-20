#!/usr/bin/env bash
# Build dist/mrc2png.app and zip it as dist/mrc2png-macos-arm64.zip.
# Usage: scripts/build_macos.sh [python]   (default: python3 on PATH)
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${1:-python3}"

rm -rf build dist
"$PY" -m PyInstaller --noconfirm --clean mrc2png.spec

# TODO(signing): sign with a Developer ID and notarize here, e.g.
#   codesign --deep --force --options runtime --sign "Developer ID Application: ..." dist/mrc2png.app
#   xcrun notarytool submit dist/mrc2png-macos-arm64.zip --keychain-profile ... --wait
#   xcrun stapler staple dist/mrc2png.app
# Until then the app is only ad-hoc signed (PyInstaller does this automatically).

# ditto keeps the bundle's symlinks and metadata intact (plain `zip` can break them).
ditto -c -k --keepParent dist/mrc2png.app dist/mrc2png-macos-arm64.zip
echo "Built dist/mrc2png.app and dist/mrc2png-macos-arm64.zip"
