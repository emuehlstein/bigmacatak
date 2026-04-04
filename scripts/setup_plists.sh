#!/bin/bash
# setup_plists.sh — Replace YOUR_USERNAME in plist templates and install
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLISTS_DIR="$SCRIPT_DIR/../plists"
USERNAME="$(whoami)"

echo "Setting up LaunchDaemons for user: $USERNAME"
echo ""

mkdir -p ~/ots/logs

for plist in "$PLISTS_DIR"/*.plist; do
    filename=$(basename "$plist")
    echo "Processing: $filename"

    # Replace YOUR_USERNAME with actual username
    sed "s|YOUR_USERNAME|$USERNAME|g" "$plist" > "/tmp/$filename"

    # Install
    sudo cp "/tmp/$filename" "/Library/LaunchDaemons/$filename"
    sudo chown root:wheel "/Library/LaunchDaemons/$filename"

    echo "  → Installed to /Library/LaunchDaemons/$filename"
done

echo ""
echo "Loading services..."
for plist in /Library/LaunchDaemons/launchd.ots-*.plist /Library/LaunchDaemons/launchd.meshcore-*.plist; do
    if [ -f "$plist" ]; then
        filename=$(basename "$plist")
        sudo launchctl load "$plist" 2>/dev/null && echo "  ✅ Loaded: $filename" || echo "  ⚠️  Already loaded: $filename"
    fi
done

echo ""
echo "Done! Check status with:"
echo "  sudo launchctl list | grep -E 'ots|meshcore|opentakserver'"
