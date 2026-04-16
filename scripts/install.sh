#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/aisisstant-tracker@vovkes"
SYSTEMD_DIR="$HOME/.config/systemd/user"

echo "=== Aisisstant Activity Tracker Installer ==="

# 1. Install Python dependencies
echo "[1/5] Installing Python dependencies..."
pip install --user --break-system-packages -r "$PROJECT_DIR/requirements.txt"

# 2. Start PostgreSQL in Docker
echo "[2/5] Starting PostgreSQL..."
cd "$PROJECT_DIR"
docker compose up -d
echo "Waiting for PostgreSQL to be ready..."
until docker compose exec -T postgres pg_isready -U aisisstant > /dev/null 2>&1; do
    sleep 1
done
echo "PostgreSQL is ready"

# 3. Install GNOME Shell extension
echo "[3/5] Installing GNOME Shell extension..."
mkdir -p "$EXT_DIR"
cp "$PROJECT_DIR/gnome-extension/extension.js" "$EXT_DIR/"
cp "$PROJECT_DIR/gnome-extension/metadata.json" "$EXT_DIR/"
echo "Extension installed to $EXT_DIR"
echo "NOTE: You need to enable it manually:"
echo "  gnome-extensions enable aisisstant-tracker@vovkes"
echo "  (or log out and back in, then enable via Extensions app)"

# 4. Install systemd service
echo "[4/5] Installing systemd user service..."
mkdir -p "$SYSTEMD_DIR"
cp "$PROJECT_DIR/systemd/aisisstant.service" "$SYSTEMD_DIR/"
systemctl --user daemon-reload

# 5. Enable and start service
echo "[5/5] Enabling and starting service..."
systemctl --user enable aisisstant.service
systemctl --user start aisisstant.service

echo ""
echo "=== Installation complete ==="
echo "Check status: systemctl --user status aisisstant"
echo "View logs:    journalctl --user -u aisisstant -f"
echo ""
echo "IMPORTANT: Enable the GNOME extension for window tracking:"
echo "  gnome-extensions enable aisisstant-tracker@vovkes"
