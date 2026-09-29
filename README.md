# minilab-sfbox

Headless SoundFont player for **Raspberry Pi 2 Model B + Arturia MiniLab 3**.
Plug in the keyboard and a speaker, power on, play — no PC needed.

- Loads every `.sf2` / `.sf3` from `soundfonts/` into one `fluidsynth` instance
- **Knob** browses instruments, **Pad 1 / Pad 2** = prev/next, any **Program Change** selects directly
- Writes the current instrument to the **MiniLab 3 display via SysEx** (DAW mode) + console log
- Upload via USB stick (auto-import), `scp`, or browser (`app.py --serve 8080`)
- Lightweight: Python stdlib + `mido`, ~30 MB RAM, Pi-2-safe fluidsynth settings

## Why not an existing project?

| Option | Verdict for Pi 2 + MiniLab 3 + SF2 |
|---|---|
| **Zynthian** | Great, but too heavy for a Pi 2 (needs Pi 3/4/5, slow boot, complex UI) |
| **SamplerBox** ([samplerbox.org](https://www.samplerbox.org/)) | Works on Pi 2 and boots in ~8 s — but plays `.wav` sample packs, not SoundFonts, no MiniLab display support |
| **Patchbox OS** | Good low-latency base, but image is stale for Pi 2 and still needs you to build the SF2+MIDI layer yourself |
| **Bare fluidsynth + KOOP/squishbox scripts** | Closest fit, but no preset browsing, no display feedback, no uploader |
| **This project** | Pi OS Lite + fluidsynth + 1 Python daemon built exactly for: SF2 upload, knob/pad browsing, MiniLab display |

## What you need

- Raspberry Pi 2 Model B, microSD (8 GB+), 5 V / 2 A supply (MiniLab draws USB power — use a good supply)
- Arturia MiniLab 3 (USB cable)
- Sound output: **USB DAC recommended** (e.g. UGREEN / Sabrent USB audio, ~$10 — much cleaner and lower-latency than the Pi 2's onboard PWM jack). Onboard 3.5 mm works for testing
- One `.sf2` to start (e.g. FluidR3_GM, GeneralUser GS, or a piano SF2)

## 1. Flash the OS

1. Raspberry Pi Imager → **Raspberry Pi OS Lite (32-bit)** (no desktop = fast boot, ~15–20 s on Pi 2)
2. In Imager settings: enable SSH, set user `pi`, configure Wi-Fi (Pi 2 has no onboard Wi-Fi — use Ethernet or a USB Wi-Fi dongle; network is only needed for setup/uploads)
3. Boot, SSH in, `sudo apt update && sudo apt upgrade -y`

## 2. Install

One-liner (on the Pi, internet required):

```bash
curl -fsSL https://raw.githubusercontent.com/FoxyIsCoding/minilab-sfbox/main/bootstrap.sh | bash
```

Re-running it later updates to the newest version. Or manually:

```bash
git clone <this-repo> && cd minilab-sfbox
chmod +x install.sh
./install.sh
```

This installs fluidsynth, Python MIDI libs, enables the `minilab-sfbox` service (auto-start on boot), and sets realtime-audio limits.

## 3. Add sounds

Any of (they combine — USB stick files are auto-copied on boot):

```bash
# a) direct copy
scp GrandPiano.sf2 pi@<pi-ip>:/opt/minilab-sfbox/soundfonts/
sudo systemctl restart minilab-sfbox

# b) USB stick: put .sf2/.sf3 on a FAT32 stick, plug into the Pi, reboot

# c) browser upload (phone/laptop on same network)
python3 /opt/minilab-sfbox/app.py --serve 8080  # then open http://<pi-ip>:8080/
```

Check what was found: `python3 /opt/minilab-sfbox/app.py --list`

## 4. Set up the MiniLab 3 (once, on a PC/Mac)

1. Install **Arturia MIDI Control Center**
2. Optional but recommended: pick one endless knob for browsing → set to **CC 16, Relative #2**, save to the User template. Note its CC into `preset_cc` + `knob_mode = relative2` in `config.ini`
3. **Switch the MiniLab to DAW program** when playing from the Pi — the display SysEx only renders reliably in DAW mode (control still works in any mode; the console log always shows the preset)
4. Defaults work with zero configuration: Pad 1/2 (notes 36/37) switch instruments

Find any control's numbers: `python3 /opt/minilab-sfbox/app.py --learn`, then twist/hit it.

## 5. Play

```
MiniLab 3 --USB--> Pi 2 --[USB DAC or 3.5mm]--> speaker
```

Power on → ~20 s → first preset auto-loads → play. Watch with `sudo journalctl -u minilab-sfbox -f`.

## Controls (all in `config.ini`)

| Input | Default | Action |
|---|---|---|
| Display encoder | CC 28 | **Browse**: moves a `>` highlight on the display, sound unchanged |
| Encoder click | — (discover yours, see below) | **Confirm**: loads the highlighted instrument + audition blip |
| Preset knob | CC 16 | Browse (highlight; click loads) |
| Any piano key | — | Auditions the highlight (loads it first, `confirm.on_note`) |
| Pad 1 / Pad 2 | notes 36 / 37 | Previous / next instrument (loads immediately) |
| Pad 7 / Pad 8 | notes 42 / 43 | Jump to first preset of prev / next **soundfont file** |
| Program Change (ch 1) | — | Direct select inside current SoundFont (configure pads to ProgChg in MCC for 1-tap favorites) |
| Fader 1 | CC 14 | Loudness: synth master gain 0–125% (+ subtle test ding when you stop moving) |
| Fader 4 | CC 31 | Stereo pan (MIDI CC10) |
| Knobs 1–8 | CC 86…117 | Reverb, Room, Damp, Chorus, Bass (sub-octave), Bright, Attack, Release |
| Everything else | — | Forwarded to fluidsynth (keys, sustain CC64, pitchbend, modwheel…) |

Finding your encoder-click message: `python3 tools/sniff.py 30`, click the
encoder a few times, then set `encoder_click_note` (or `encoder_click_cc`)
in `config.ini` to what you see.

Switching soundfonts: all `.sf2` files form one long preset list, so the
encoder and pads 1/2 cross file boundaries automatically; pads 7/8 jump
straight to the next file. The display always shows which file you're in
(`sf` line + `i/N name`).

Volume note: the fader drives fluidsynth's master gain (`backend = fluid`,
0–125%), not the Pi's hardware mixer — the Pi's onboard control is named
`Headphone`, not `Master`, which is why mixer-based volume silently did
nothing. Set `backend = amixer` + the right `control` if you prefer OS-level
volume (capped at 100%).

Display shows `SoundFont  i/N  PresetName` (scroll view for large lists, knob graphic for ≤128 presets). Last preset is remembered across reboots (`state.json`).

## Pi 2 latency notes

Defaults are conservative for the Pi 2's Cortex-A7: 44.1 kHz, 64-voice polyphony, 128-sample periods. If you hear crackles: use the USB DAC (`audio_device = hw:Device`), keep to one modest-size SF2, don't raise polyphony. HDMI output stays on by default here (unlike some scripts) so a plugged monitor still works — disable it in `/boot/config.txt` if you need the last mA.

## Big soundfont libraries (lazy loading)

All `.sf2` files are parsed at boot (names only — instant), but only files
under `[library] preload_max_mb` (default 8 MB) are loaded into RAM. Big
files load **in the background the first time you select them** — the display
shows the target preset instantly, sound follows in a few seconds — and stay
cached until total background samples exceed `[library] mem_cap_mb`
(default 256 MB, safe on a 1 GB Pi 2), when the least-recently-used file
unloads automatically. First boot with 400 MB of samples takes seconds, not
minutes, and RAM stays bounded.

## Unplug-proofing

Boot with nothing plugged in, yank the keyboard or the USB DAC mid-session —
the app waits for MIDI ports, respawns fluidsynth if the audio device
vanishes, and reconnects everything when devices return. No systemd
crash-loop, no restart needed.

## Wi-Fi on the Pi 2 (no onboard wireless)

- Only needed for setup/uploads — the box plays fully offline.
- **TL-WN821N: check the hardware version first** (`lsusb` on any Linux box).
  v1–v3 (Atheros) work with `sudo apt install firmware-atheros`;
  v4 (RTL8192CU) works out of the box but wants a powered hub;
  **v5/v6 (RTL8192EU) have no mainline driver** — TP-Link's own driver only
  supports kernels ≤4.9, so on current Pi OS you'd be compiling a community
  DKMS driver on a Pi 2 (slow, breaks on kernel updates). Avoid.
- Safest cheap options: RTL8188CUS / RTL8192CU nano dongles (in-kernel), or
  phone USB tethering for the 5 minutes setup takes.

## Files

| File | What |
|---|---|
| `app.py` | Daemon: MIDI routing, preset logic, uploader, `--learn`/`--list` |
| `minilab_display.py` | MiniLab 3 SysEx builders (display + pad colors) |
| `sf2.py` | Zero-dependency SF2/SF3 preset-name reader |
| `fluid.py` | FluidSynth command builder + telnet `select` control |
| `config.ini` | All bindings and audio settings |
| `install.sh` | Pi setup (packages, RT limits, service) |
| `minilab-sfbox.service` | systemd unit |
| `tests/test_basic.py` | Hardware-free self-tests |

## Troubleshooting

- **No sound**: `aplay -l`, check `audio_device` in `config.ini`; `alsamixer` (F6 → USB card) unmute/raise; `aconnect -l` should show `FLUID Synth`
- **MiniLab not found**: `aconnect -l` / `python3 app.py --learn`; try another USB port / powered hub (Pi 2 ports are weak)
- **Display doesn't update**: switch MiniLab to DAW program; control still works regardless
- **Underruns on big SF2s**: smaller SoundFont, `polyphony = 48`, USB DAC, `sudo systemctl stop` anything else
