#!/bin/bash
# bootstrap.sh — one-liner remote installer for minilab-sfbox.
# Run ON the Raspberry Pi:
#   curl -fsSL https://raw.githubusercontent.com/FoxyIsCoding/minilab-sfbox/main/bootstrap.sh | bash
# Re-running it later updates to the latest version.
set -euo pipefail

REPO="${SFBOX_REPO:-https://github.com/FoxyIsCoding/minilab-sfbox.git}"
BRANCH="${SFBOX_BRANCH:-main}"
APP=/opt/minilab-sfbox

if ! command -v git >/dev/null 2>&1; then
  echo "== installing git =="
  sudo apt update && sudo apt install -y git
fi

if [ -d "$APP/.git" ]; then
  echo "== updating $APP =="
  git -C "$APP" fetch --depth 1 origin "$BRANCH"
  git -C "$APP" checkout -q "$BRANCH"
  git -C "$APP" reset -q --hard "origin/$BRANCH"
else
  echo "== cloning into $APP =="
  sudo rm -rf "$APP"
  sudo git clone --depth 1 --branch "$BRANCH" "$REPO" "$APP"
fi
sudo chown -R "$(whoami):$(whoami)" "$APP"
bash "$APP/install.sh"
