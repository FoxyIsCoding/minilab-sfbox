#!/bin/bash
# install.sh — run ON the Raspberry Pi (Pi OS Lite 32-bit) as user pi.
# Installs fluidsynth, Python MIDI deps, the service, and RT/low-latency tweaks.
set -euo pipefail
APP=/opt/minilab-sfbox
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "== apt packages (fluidsynth, alsa, python midi) =="
sudo apt update
sudo apt install -y fluidsynth alsa-utils python3-pip python3-mido python3-rtmidi

echo "== install app to $APP =="
if [ "$HERE" != "$APP" ]; then
  sudo mkdir -p "$APP"
  sudo cp "$HERE/app.py" "$HERE/minilab_display.py" "$HERE/sf2.py" "$HERE/fluid.py" \
          "$HERE/config.ini" "$HERE/bootstrap.sh" "$APP/"
  sudo mkdir -p "$APP/soundfonts" "$APP/tests" "$APP/tools"
  sudo cp "$HERE/tests/test_basic.py" "$APP/tests/" 2>/dev/null || true
  sudo cp "$HERE/tools/sniff.py" "$APP/tools/" 2>/dev/null || true
  sudo cp "$HERE/minilab-sfbox.service" "$APP/" 2>/dev/null || true
  sudo chown -R "$(whoami):$(whoami)" "$APP"
else
  echo "(already in $APP, skipping copy)"
fi

echo "== self-test =="
python3 "$APP/tests/test_basic.py" | tail -1 || true

echo "== audio device detect =="
cat /proc/asound/cards || true
USB_CARD=$(grep -m1 -oP '\[\K[^\]]+' /proc/asound/cards || true)
echo "First card name: '${USB_CARD:-?}'"
echo "TIP: for a USB DAC set audio_device = hw:Device (or hw:<name>) in $APP/config.ini"
echo "     'default' also works and follows the ALSA default."

echo "== realtime audio tweaks =="
sudo usermod -a -G audio "$(whoami)" || true
if ! grep -q "@audio - rtprio" /etc/security/limits.conf 2>/dev/null; then
  echo "@audio - rtprio 80" | sudo tee -a /etc/security/limits.conf >/dev/null
  echo "@audio - memlock unlimited" | sudo tee -a /etc/security/limits.conf >/dev/null
fi

echo "== systemd service =="
sudo cp "$HERE/minilab-sfbox.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable minilab-sfbox
echo "autostart on boot: $(sudo systemctl is-enabled minilab-sfbox 2>/dev/null || echo FAILED)"
sudo systemctl restart minilab-sfbox || true
sleep 2
sudo systemctl status minilab-sfbox --no-pager || true

echo "== console autologin (Pi boots straight to a logged-in shell) =="
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_boot_behaviour B2 || true
  echo "boot behaviour now: $(sudo raspi-config nonint get_boot_behaviour 2>/dev/null || echo ?)"
  echo "(systemd starts minilab-sfbox at boot regardless; autologin is for the local console)"
else
  echo "(raspi-config not found — not a Pi? skipping autologin)"
fi

echo
echo "DONE. Next steps:"
echo " 1. Put .sf2/.sf3 files in $APP/soundfonts/ (USB stick, scp, or app.py --serve 8080)"
echo " 2. Plug MiniLab 3 into a Pi USB port, speaker/DAC into audio out"
echo " 3. sudo journalctl -u minilab-sfbox -f   (watch preset changes)"
echo " 4. python3 $APP/app.py --learn           (find your knob's CC, set preset_cc)"
