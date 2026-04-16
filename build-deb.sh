#!/bin/bash
#
# Build the Aisisstant .deb package.
#
# Usage:
#   ./build-deb.sh          # build the .deb
#   ./build-deb.sh install  # build and install
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Checking build dependencies ==="
MISSING=""
for pkg in debhelper dh-python python3-all python3-setuptools devscripts; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        MISSING="$MISSING $pkg"
    fi
done

if [ -n "$MISSING" ]; then
    echo "Installing missing build dependencies:$MISSING"
    sudo apt-get update
    sudo apt-get install -y $MISSING
fi

echo ""
echo "=== Building .deb package ==="
dpkg-buildpackage -us -uc -b

echo ""
echo "=== Build complete ==="
DEB=$(ls -1t ../aisisstant_*.deb 2>/dev/null | head -1)

if [ -z "$DEB" ]; then
    echo "ERROR: .deb file not found"
    exit 1
fi

echo "Package: $DEB"
echo ""
dpkg-deb -I "$DEB"
echo ""
echo "To install:  sudo dpkg -i $DEB && sudo apt-get install -f"

if [ "${1:-}" = "install" ]; then
    echo ""
    echo "=== Installing ==="
    sudo dpkg -i "$DEB"
    sudo apt-get install -f -y
    echo ""
    echo "Done! Launch 'Aisisstant Settings' from your app menu."
fi
