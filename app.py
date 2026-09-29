#!/usr/bin/env python3
"""minilab-sfbox: headless SF2/SF3 player for Raspberry Pi 2 + MiniLab 3.

- Loads every .sf2/.sf3 in soundfonts/ into one fluidsynth instance.
- Forwards MiniLab notes/CC to fluidsynth, intercepts preset controls.
- Shows current instrument on the MiniLab display via SysEx (DAW mode).
- Upload: drop files in soundfonts/, plug a FAT32 USB stick with .sf2,
  SCP them over, or run with --serve and use the browser uploader.

Controls (defaults, all editable in config.ini):
  Pad 1 (note 36) ......... previous instrument
  Pad 2 (note 37) ......... next instrument
  Knob CC 16 .............. browse instruments (Absolute, or set the knob
                              to Relative #2 in Arturia MCC + knob_mode=relative2)
  Any Program Change ...... direct select within current soundfont
  --learn ................. prints incoming MIDI so you can find your knob's CC
"""

import argparse
import configparser
import fnmatch
import glob
import json
import os
import queue
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sf2 as sf2mod
import fluid as fluidmod
import minilab_display as disp

CONFIG_PATH = os.path.join(HERE, "config.ini")
STATE_PATH = os.path.join(HERE, "state.json")


# ---------------- config ----------------

def load_config(path=CONFIG_PATH):
    c = configparser.ConfigParser()
    c.read(path)
    return c


def cfg_get(c, s, k, fallback):
    try:
        return c.get(s, k, fallback=fallback)
    except Exception:
        return fallback


# ---------------- library ----------------

def find_soundfonts(soundfont_dir):
    files = []
    for ext in ("*.sf2", "*.SF2", "*.sf3", "*.SF3"):
        files.extend(glob.glob(os.path.join(soundfont_dir, ext)))
    return sorted(files)


def import_from_usb(soundfont_dir, patterns=("/media/*/*.sf2", "/media/*/*.sf3",
                                             "/mnt/usb/*.sf2", "/media/usb/*.sf2")):
    imported = []
    for pat in patterns:
        for src in glob.glob(pat):
            dst = os.path.join(soundfont_dir, os.path.basename(src))
            if not os.path.exists(dst):
                try:
                    shutil.copy2(src, dst)
                    imported.append(dst)
                except OSError:
                    pass
    return imported


def build_library(sf_files):
    """Flatten to [{'sfont':1-based, 'file':..,'bank':..,'prog':..,'name':..}]."""
    lib = []
    for i, f in enumerate(sf_files, start=1):
        presets = sf2mod.list_presets_sf2(f)
        if not presets:  # unparsable -> expose GM 0..127 blind
            presets = [(0, p, f"Program {p}") for p in range(128)]
        short = os.path.splitext(os.path.basename(f))[0][:14]
        for bank, prog, name in presets:
            lib.append({"sfont": i, "file": f, "bank": bank, "prog": prog,
                        "sf": short, "name": name})
    return lib


# ---------------- tiny uploader (stdlib only) ----------------

UPLOAD_PAGE = b"""<html><body style="font-family:sans-serif;max-width:600px;margin:2em auto">
<h2>minilab-sfbox upload</h2>
<p>Upload .sf2 / .sf3, then reboot (or restart service). Max ~200MB per file.</p>
<form method=POST enctype=multipart/form-data>
<input type=file name=file accept=".sf2,.sf3,.SF2,.SF3"><input type=submit value=Upload>
</form><p><a href="/list">/list</a> shows loaded soundfonts.</p></body></html>"""


class UploadHandler(BaseHTTPRequestHandler):
    soundfont_dir = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/list":
            files = find_soundfonts(self.soundfont_dir)
            body = "\n".join(os.path.basename(f) for f in files).encode() or b"(empty)"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(UPLOAD_PAGE)))
            self.end_headers()
            self.wfile.write(UPLOAD_PAGE)

    def do_POST(self):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self.send_response(400)
            self.end_headers()
            return
        try:
            boundary = ctype.split("boundary=")[1].encode()
        except IndexError:
            self.send_response(400)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        if length > 300 * 1024 * 1024:
            self.send_response(413)
            self.end_headers()
            return
        data = self.rfile.read(length)
        # minimal multipart parse: filename + payload between blank line and boundary
        try:
            h_end = data.index(b"\r\n\r\n")
            fname = "upload.sf2"
            header = data[:h_end].decode("latin1")
            for line in header.split("\r\n"):
                if "filename=" in line:
                    fname = line.split("filename=")[1].strip('"; ')
                    fname = os.path.basename(fname) or fname
            payload = data[h_end + 4:]
            tail = payload.rfind(b"\r\n--" + boundary)
            if tail != -1:
                payload = payload[:tail]
                if payload.endswith(b"\r\n"):
                    payload = payload[:-2]
            if not fname.lower().endswith((".sf2", ".sf3")):
                raise ValueError("need .sf2/.sf3")
            with open(os.path.join(self.soundfont_dir, fname), "wb") as f:
                f.write(payload)
            body = f"saved {fname} ({len(payload)} bytes). Restart service.\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = f"upload failed: {e}\n".encode()
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def serve_upload(soundfont_dir, port):
    UploadHandler.soundfont_dir = soundfont_dir
    httpd = HTTPServer(("0.0.0.0", port), UploadHandler)
    httpd.serve_forever()


# ---------------- MIDI helpers ----------------

def find_ports(mido, keywords):
    ins = [n for n in mido.get_input_names()]
    outs = [n for n in mido.get_output_names()]
    kws = [k.strip().lower() for k in keywords if k.strip()]

    def match(names):
        for n in names:
            ln = n.lower()
            if any(k in ln for k in kws):
                return n
        return None

    return ins, outs, match(ins), match(outs)


def rel2_delta(value: int) -> int:
    """Arturia Relative #2 (2's complement): 1=+1 ... 127=-1, 0/64=no-op."""
    if value == 0 or value == 64:
        return 0
    return value if value < 64 else value - 128


def fx_accumulate(current: int, msg_value: int, knob_mode: str,
                  top: int = 127) -> int:
    """Next 0..top accumulator for an FX knob (absolute sets, relative nudges)."""
    if knob_mode.startswith("rel"):
        return max(0, min(top, current + rel2_delta(msg_value)))
    return max(0, min(top, round(msg_value / 127 * top)))


def fx_pct(value: int) -> int:
    """FX knob value -> percent of unity. 127 == 100%, so boosters read >100%."""
    return round(value / 127 * 100)


def bass_levels(knob_value: int, velocity: int):
    """Bass booster knob (0..254, unity 127) -> [(octave_shift, velocity)].

    0-100%  : one sub-octave (-12), scaled with the knob.
    100-150%: -12 at full + a second layer (-24) fading in.
    150-200%: -24 up to 80% of the played velocity, for a proper deep boost.
    """
    pct = fx_pct(knob_value)
    subs = []
    if pct > 0 and velocity > 0:
        v1 = int(velocity * min(1.0, pct / 100))
        if v1 > 0:
            subs.append((-12, min(127, v1)))
        if pct > 100:
            v2 = int(velocity * 0.8 * min(1.0, (pct - 100) / 100))
            if v2 > 0:
                subs.append((-24, min(127, v2)))
    return subs


def pan_label(v: int) -> str:
    if v < 60:
        return f"L{round((64 - v) / 64 * 100)}"
    if v > 68:
        return f"R{round((v - 64) / 63 * 100)}"
    return "Center"


def build_volume_cmd(pct: int, backend: str = "auto",
                     card: str = "default", control: str = "Master"):
    """argv to set OS volume, or None if no backend tool is available."""
    import shutil as _sh
    pct = max(0, min(100, int(pct)))
    if backend in ("auto", "amixer") and _sh.which("amixer"):
        if backend == "auto" or True:
            return ["amixer", "-q", "-c", card, "sset", control, f"{pct}%"]
    if backend in ("auto", "pactl") and _sh.which("pactl"):
        return ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"]
    return None


# knob name -> (fluidsynth-shell action, needs-all-channels-CC)
FX_DEFS = ("reverb", "room", "damp", "chorus",
           "bass", "bright", "attack", "release")
FX_TITLES = {"reverb": "Reverb", "room": "RoomSize", "damp": "Damp",
             "chorus": "Chorus", "bass": "BassBoost", "bright": "Bright",
             "attack": "Attack", "release": "Release"}

# display modes reachable from the encoder menu (double-click the encoder).
# (key, label, one-line help shown under the highlighted entry)
MODES = (
    ("instruments", "Instruments", "browse all presets"),
    ("soundfonts", "SoundFonts", "jump to a whole .sf2"),
    ("favorites", "Favorites", "your starred presets"),
    ("star", "* Star", "favourite the current one"),
    ("volume", "Master Vol", "encoder = loudness 0-125%"),
    ("close", "Close", "back to playing"),
)
MODE_HELP = {k: h for k, _, h in MODES}
MODE_LABEL = {k: n for k, n, _ in MODES}


def fav_key(p):
    """Stable favourite identity (survives library rebuilds/reorderings)."""
    return f"{os.path.basename(p['file'])}|{p['bank']}|{p['prog']}"


def partition_files(files_sizes, preload_max_mb: float):
    """Split [(path, bytes)] into (preload, lazy) by size threshold."""
    preload, lazy = [], []
    for path, size in files_sizes:
        (preload if size <= preload_max_mb * 1024 * 1024 else lazy).append(path)
    return preload, lazy


def choose_evictions(loaded_order, current_path, cap_bytes: int):
    """loaded_order: [(path, bytes)] oldest-first. Returns paths to drop
    (never the current file) so the total fits cap_bytes."""
    total = sum(s for _, s in loaded_order)
    evict = []
    for path, size in loaded_order:
        if total <= cap_bytes:
            break
        if path == current_path:
            continue
        evict.append(path)
        total -= size
    return evict


# ---------------- player ----------------

class Player:
    def __init__(self, config):
        self.c = config
        self.sf_dir = os.path.join(
            HERE, cfg_get(config, "fluid", "soundfont_dir", "soundfonts"))
        os.makedirs(self.sf_dir, exist_ok=True)
        import_from_usb(self.sf_dir)
        self.sf_files = find_soundfonts(self.sf_dir)
        self.lib = build_library(self.sf_files)
        self.idx = 0
        self.bank_msb = 0
        self.bank_lsb = 0
        self.mido = None
        self.inport = None
        self.fs_out = None
        self.ml_out = None
        self.fluid = fluidmod.FluidControl()
        self.proc = None

        s = cfg_get(config, "controls", "knob_mode", "absolute")
        self.knob_mode = s.strip().lower()
        self.preset_cc = int(cfg_get(config, "controls", "preset_cc", "16"))
        # main encoder below the display (usually relative CC)
        self.encoder_cc = int(cfg_get(config, "controls", "encoder_cc", "0"))
        self.encoder_mode = cfg_get(config, "controls", "encoder_mode",
                                    "auto").strip().lower()
        # The "click" control: the MiniLab 3's main encoder sends nothing when
        # pressed (verified 2026-09-29: 5 presses, 1 hold, 1 double-press all
        # produced zero MIDI), so a spare pad is the click by default.
        # Units whose encoder *does* report a press can use click_cc instead.
        self.click_note = int(cfg_get(config, "controls", "click_note", "38"))
        self.click_cc = int(cfg_get(config, "controls", "click_cc", "0"))
        # click timing (ms): single = confirm, double = menu, long = favourite
        self.click_single_ms = int(cfg_get(config, "controls", "click_single_ms", "350"))
        self.click_double_ms = int(cfg_get(config, "controls", "click_double_ms", "400"))
        self.click_long_ms = int(cfg_get(config, "controls", "click_long_ms", "800"))
        self.pad_channel = int(cfg_get(config, "controls", "pad_channel", "9"))
        self.confirm_on_note = cfg_get(config, "confirm", "on_note",
                                       "true").strip().lower() in ("1", "true", "yes", "on")
        # audition blip after a preset loads + subtle ding for volume test
        self.pv_enabled = cfg_get(config, "preview", "enabled",
                                  "true").strip().lower() in ("1", "true", "yes", "on")
        self.pv_note = int(cfg_get(config, "preview", "note", "72"))
        self.pv_vel = int(cfg_get(config, "preview", "vel", "70"))
        self.pv_ms = int(cfg_get(config, "preview", "ms", "250"))
        self.ding_enabled = cfg_get(config, "preview", "ding",
                                    "true").strip().lower() in ("1", "true", "yes", "on")
        self.ding_note = int(cfg_get(config, "preview", "ding_note", "84"))
        self.ding_vel = int(cfg_get(config, "preview", "ding_vel", "40"))
        self.ding_ms = int(cfg_get(config, "preview", "ding_ms", "150"))
        self.prev_note = int(cfg_get(config, "controls", "prev_note", "36"))
        self.next_note = int(cfg_get(config, "controls", "next_note", "37"))
        # pads 7/8 jump between soundfont files (notes are 0-based MIDI numbers)
        self.sf_prev_note = int(cfg_get(config, "controls", "sf_prev_note", "42"))
        self.sf_next_note = int(cfg_get(config, "controls", "sf_next_note", "43"))
        self.midi_ch = int(cfg_get(config, "controls", "midi_channel", "0"))
        self.display_mode = cfg_get(config, "display", "mode", "daw").strip().lower()

        # system volume fader
        self.vol_cc = int(cfg_get(config, "volume", "cc", "0"))
        self.vol_backend = cfg_get(config, "volume", "backend", "auto")
        self.vol_card = cfg_get(config, "volume", "card", "default")
        self.vol_control = cfg_get(config, "volume", "control", "Master")
        try:
            self.vol_cooldown = int(cfg_get(config, "volume", "cooldown_ms", "40")) / 1000
        except ValueError:
            self.vol_cooldown = 0.04
        self._vol_last_pct = -1
        self._vol_last_t = 0.0

        # pan fader (MIDI CC10 on all channels)
        self.pan_cc = int(cfg_get(config, "pan", "cc", "0"))
        self.pan = 64

        # fx knobs: name -> cc, accumulator 0..fx_top[name]
        self.fx_cc = {}
        self.fx_val = {}
        self.fx_top = {}
        for name in FX_DEFS:
            cc = int(cfg_get(config, "fx", f"cc_{name}", "0"))
            top = max(1, int(cfg_get(config, "fx", f"top_{name}", "127")))
            self.fx_cc[name] = cc
            self.fx_top[name] = top
            # start position: def_{name} percent of travel (50% unless set),
            # where 100% of unity sits at 50% travel for over-range boosters.
            pct0 = int(cfg_get(config, "fx", f"def_{name}",
                               "25" if top > 127 else "50"))
            self.fx_val[name] = max(0, min(top, round(top * pct0 / 100)))
        self.shell = fluidmod.FluidShell()
        self._subs = {}  # (channel, note) -> [sub notes] (bass booster layers)
        # display menu: mode browsing, favourites
        self.mode = cfg_get(config, "menu", "mode", "instruments").strip().lower()
        if self.mode not in MODE_LABEL:
            self.mode = "instruments"
        self.menu_open = False
        self.menu_idx = [k for k, _, _ in MODES].index(self.mode)
        self.favs = []  # library indices, kept sorted + de-duplicated
        self._press = None      # pending click: {'t':..,'long':bool}
        self._last_click_t = 0.0
        self._menu_hold_until = 0.0
        self._menu_saved = None  # (mode,) restored when leaving the menu
        # background preset loader: display updates instantly, synth loads async
        self._load_q = queue.Queue(maxsize=8)
        self._last_show = 0.0
        self._last_save = 0.0
        self.pending_idx = None  # highlighted (not yet sounding) preset
        self._pending_offs = []  # [(deadline, channel, note)] note-offs to send
        self._vol_ding_at = 0.0  # last volume change (for idle ding)
        self._vol_ding_armed = False
        # lazy soundfont loading: small files preload, big ones on demand
        try:
            preload_max = float(cfg_get(config, "library", "preload_max_mb", "8"))
        except ValueError:
            preload_max = 8.0
        try:
            self.mem_cap = int(cfg_get(config, "library", "mem_cap_mb", "256")) * 1024 * 1024
        except ValueError:
            self.mem_cap = 256 * 1024 * 1024
        sizes = []
        for f in self.sf_files:
            try:
                sizes.append((f, os.path.getsize(f)))
            except OSError:
                sizes.append((f, 0))
        self.preload_files, self.lazy_files = partition_files(sizes, preload_max)
        self.preload_ids = {}  # path -> sfont id (1-based cmdline order)
        self.dyn_ids = {}      # path -> live-loaded sfont id
        self.dyn_order = []    # paths oldest-first (LRU)
        self.dyn_bytes = 0
        self._font_lock = threading.Lock()
        self._font_gen = 0     # bumped on synth respawn; stale loads discarded
        self.fluid_log = os.path.join(HERE, "fluid.log")
        # last: restore saved index/mode/favourites now that every knob,
        # the menu and the library all exist
        self._load_state()

    # -- state --
    def _load_state(self):
        self.favs = []
        try:
            with open(STATE_PATH) as f:
                st = json.load(f)
            self.idx = max(0, min(len(self.lib) - 1, int(st.get("index", 0))))
            mode = st.get("mode", "instruments")
            if mode in MODE_LABEL:
                self.mode = mode
                self.menu_idx = [k for k, _, _ in MODES].index(mode)
            self._restore_favs(st.get("favs", []))
        except Exception:
            self.idx = 0
        self._sync_highlight()

    def _restore_favs(self, keys):
        """Re-attach saved favourite keys to this build's library indices."""
        by_key = {fav_key(p): i for i, p in enumerate(self.lib)}
        got = [by_key[k] for k in keys if k in by_key]
        self.favs = sorted(set(got))

    def _save_state(self):
        try:
            with open(STATE_PATH, "w") as f:
                json.dump({"index": self.idx,
                           "mode": self.mode,
                           "favs": [fav_key(self.lib[i])
                                    for i in sorted(set(self.favs))
                                    if i < len(self.lib)]}, f)
        except OSError:
            pass

    # -- favourites --
    def is_fav(self, idx: int) -> bool:
        return idx in self.favs

    def toggle_fav(self, idx: int) -> bool:
        """Star/unstar a library index. Returns the new state."""
        if idx is None or not (0 <= idx < len(self.lib)):
            return False
        if idx in self.favs:
            self.favs.remove(idx)
            new = False
        else:
            self.favs.append(idx)
            self.favs.sort()
            new = True
        self._save_state()
        p = self.lib[idx]
        print(f"{'favourited' if new else 'unfavourited'}: "
              f"{p['sf']} | {p['name']} ({len(self.favs)} total)", flush=True)
        self.popup("Favourites",
                   ("* " if new else "- ") + p["name"][:24],
                   len(self.favs) * 127 // max(1, len(self.lib)))
        return new

    # -- fluidsynth --
    def start_fluid(self):
        audio = cfg_get(self.c, "fluid", "audio_device", "default")
        gain = cfg_get(self.c, "fluid", "gain", "0.8")
        poly = cfg_get(self.c, "fluid", "polyphony", "64")
        try:
            sr = int(cfg_get(self.c, "fluid", "samplerate", "44100"))
        except ValueError:
            sr = 44100
        if not self.sf_files:
            print("No .sf2/.sf3 in soundfonts/. "
                  "Drop one in and restart.", flush=True)
            return None
        # only small files preload; big ones load on demand (fast boot, low RAM)
        cmd = fluidmod.build_command(self.preload_files, audio, gain, sr, poly)
        print(f"fluidsynth: preloading {len(self.preload_files)} file(s), "
              f"{len(self.lazy_files)} lazy", flush=True)
        print("fluidsynth:", " ".join(cmd), flush=True)
        self.preload_ids = {f: i + 1 for i, f in enumerate(self.preload_files)}
        self.proc = fluidmod.spawn(cmd, log_path=getattr(self, "fluid_log", None))
        return self.proc

    def _reap_fluid(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
        except Exception:
            pass

    def _fluid_log_tail(self, n=5):
        try:
            with open(getattr(self, "fluid_log", "/dev/null"), "rb") as f:
                lines = f.read().splitlines()[-n:]
            return "; ".join(l.decode(errors="replace")[:160] for l in lines)
        except OSError:
            return ""

    # -- dynamic soundfont set (loader thread owns _resolve_font) --
    def _resolve_font(self, path: str):
        """Return live sfont id for path, background-loading + enforcing
        the memory cap if needed. None on failure or stale generation."""
        with self._font_lock:
            if path in self.preload_ids:
                return self.preload_ids[path]
            if path in self.dyn_ids:
                if path in self.dyn_order:
                    self.dyn_order.remove(path)
                self.dyn_order.append(path)
                return self.dyn_ids[path]
        gen = self._font_gen
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        print(f"loading {os.path.basename(path)} "
              f"({size / 1048576:.0f}MB) in background...", flush=True)
        sid = self.shell.load_font(path)
        with self._font_lock:
            if sid is None or gen != self._font_gen:
                return None
            self.dyn_ids[path] = sid
            if path in self.dyn_order:
                self.dyn_order.remove(path)
            self.dyn_order.append(path)
            self.dyn_bytes += size
            cur = self.current()
            cur_path = cur["file"] if cur else None
            order = [(p, self._size_of(p)) for p in self.dyn_order]
            for victim in choose_evictions(order, cur_path, self.mem_cap):
                vid = self.dyn_ids.pop(victim, None)
                if victim in self.dyn_order:
                    self.dyn_order.remove(victim)
                self.dyn_bytes = max(0, self.dyn_bytes - self._size_of(victim))
                if vid is not None:
                    print(f"unloading {os.path.basename(victim)} "
                          f"(memory cap)", flush=True)
                    self.shell.unload_font(vid)
            return sid

    def _size_of(self, path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _reset_dyn(self):
        with self._font_lock:
            self.dyn_ids = {}
            self.dyn_order = []
            self.dyn_bytes = 0
            self._font_gen += 1

    def _warm_current(self):
        """After (re)start, background-load the current file if not loaded."""
        cur = self.current()
        if cur is None:
            return
        with self._font_lock:
            known = cur["file"] in self.preload_ids or cur["file"] in self.dyn_ids
        if not known:
            self._enqueue_load(self.idx, cur)

    # -- midi ports --
    def open_ports(self):
        import mido
        mido.set_backend("mido.backends.rtmidi")
        self.mido = mido
        kws = cfg_get(self.c, "midi", "input_keywords",
                      "minilab").split(",")
        fws = cfg_get(self.c, "midi", "fluid_keywords",
                      "fluid,synth").split(",")
        ins, outs, _, _ = find_ports(mido, kws)
        fins = [n for n in mido.get_output_names()
                if any(k.strip().lower() in n.lower() for k in fws)]
        print(f"MIDI in: {ins}", flush=True)
        print(f"MIDI out: {outs}", flush=True)
        # wait for both MiniLab and fluidsynth ports, forever if needed:
        # at boot the keyboard may be plugged in late (or not at all yet).
        waits = 0
        while True:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(
                    f"fluidsynth exited (code {self.proc.returncode}). "
                    "Run the fluidsynth command from the log manually to see why.")
            ins, outs, ml_in, ml_out_n = find_ports(mido, kws)
            fs_names = [n for n in mido.get_output_names()
                        if any(k.strip().lower() in n.lower() for k in fws)]
            if ml_in and fs_names:
                break
            waits += 1
            if waits % 30 == 1:
                print("waiting for MiniLab + fluidsynth MIDI ports... "
                      f"({waits}s)", flush=True)
            time.sleep(1)
        self.inport = mido.open_input(ml_in)
        self._ml_in_name = ml_in
        # separate output handles: fluidsynth (notes) + minilab (sysex display)
        self.fs_out = mido.open_output(fs_names[0])
        try:
            self.ml_out = mido.open_output(ml_out_n)
        except Exception:
            self.ml_out = None
        print(f"using in={ml_in} fluid={fs_names[0]} display={ml_out_n}",
              flush=True)

    # -- preset apply --
    def current(self):
        if not self.lib:
            return None
        return self.lib[self.idx]

    def apply(self, idx=None, quiet=False):
        """Select preset: display + state instantly, synth load in background.

        quiet=True (encoder spins): idx advances and load is queued every
        call, but display/state writes are throttled so 40+ msgs/s stay fluid.
        """
        if not self.lib:
            return
        if idx is not None:
            self.idx = max(0, min(len(self.lib) - 1, idx))
        p = self.lib[self.idx]
        self.pending_idx = None  # a direct load always clears the highlight
        now = time.time()
        if not quiet or now - self._last_show >= 0.03:
            self.show(p)
            self._last_show = now
        if not quiet or now - self._last_save >= 1.0:
            self._save_state()
            self._last_save = now
        if not quiet:
            self._push_channel_ccs()
        self._enqueue_load(self.idx, p)

    def _enqueue_load(self, idx: int, p: dict):
        q = getattr(self, "_load_q", None)
        if q is None:
            return
        item = (idx, p["file"], p["bank"], p["prog"])
        try:
            q.put_nowait(item)
        except queue.Full:
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(item)
            except queue.Full:
                pass

    # -- click-to-confirm browsing --
    def _browse_to(self, idx: int):
        """Highlight a preset (display only). Sound changes on confirm."""
        if not self.lib:
            return
        self.pending_idx = max(0, min(len(self.lib) - 1, idx))
        now = time.time()
        if now - self._last_show >= 0.03:  # keep fast spins fluid
            p = dict(self.lib[self.pending_idx])
            p["_i"] = self.pending_idx
            self.show(p, pending=True)
            self._last_show = now

    def _confirm(self, via: str = ""):
        """Load the highlighted preset (no-op when nothing pending)."""
        if self.pending_idx is None or not self.lib:
            return False
        if via:
            print(f"confirmed via {via}: ", flush=True, end="")
        if self.mode == "soundfonts":
            p = self.lib[self.pending_idx]
            print(f"soundfont -> {p['file']}", flush=True)
            self.apply(self.pending_idx)
            self.mode = "instruments"
            self._save_state()
            self._sync_highlight()
            return True
        self.apply(self.pending_idx)
        self.pending_idx = None
        return True

    # -- encoder click gestures: single = confirm, double = menu, long = star --
    def _click_press(self):
        """Handle a press of the encoder's push button."""
        now = time.time()
        if self._press is not None:  # second press while waiting = double
            self._press = None
            return self._double_click()
        self._press = {"t": now, "long": False}
        return False

    def _double_click(self):
        self._last_click_t = 0.0
        if self.menu_open:
            self._close_menu()
        else:
            self._open_menu()
        return True

    def _click_tick(self):
        """Resolve a held encoder press into long-press (favourite)."""
        p = self._press
        if p is None or p["long"]:
            return
        held = (time.time() - p["t"]) * 1000
        if held < self.click_long_ms:
            return
        self._press = None
        self._last_click_t = 0.0
        if self.menu_open:
            return
        print(f"long press ({held:.0f}ms) -> favourite", flush=True)
        self.toggle_fav(self.pending_idx if self.pending_idx is not None
                        else self.idx)

    def _click_release(self):
        """Handle the release: single click unless a double/long already ran."""
        p = self._press
        if p is None:
            return False
        self._press = None
        held = (time.time() - p["t"]) * 1000
        if held >= self.click_long_ms:
            return True  # long press already handled
        now = time.time()
        if self._last_click_t and (now - self._last_click_t) * 1000 < \
                self.click_double_ms:
            self._last_click_t = 0.0
            return self._double_click()
        self._last_click_t = now
        if self.menu_open:
            self._menu_choose()
        else:
            self._confirm("encoder click")
        return True

    def _push_channel_ccs(self):
        """Re-send sticky per-channel CCs (tone + pan) on all channels."""
        if self.fs_out is None:
            return
        try:
            for ch in range(16):
                self.fs_out.send(self.mido.Message(
                    "control_change", channel=ch, control=10, value=self.pan))
                for cc, name in ((74, "bright"), (73, "attack"), (72, "release")):
                    if self.fx_cc.get(name):
                        self.fs_out.send(self.mido.Message(
                            "control_change", channel=ch, control=cc,
                            value=self.fx_val[name]))
        except Exception:
            pass

    def popup(self, title: str, text: str, value: int):
        """Stock-looking knob popup on the MiniLab display (autohide)."""
        print(f"{title}: {text}", flush=True)
        self._send(disp.msg_info(title[:16], text[:28],
                                 max(0, min(127, value)),
                                 control="knob", autohide=True))

    # -- volume / pan / fx actions (all consumed, not forwarded) --
    def _do_volume(self, v127: int):
        import subprocess as _sp
        pct = round(v127 / 127 * 125)  # 0..125%
        now = time.time()
        if pct != self._vol_last_pct and now - self._vol_last_t >= self.vol_cooldown:
            backend = self.vol_backend
            if backend == "auto":
                backend = "fluid"  # always available, no mixer-name guessing
            if backend == "fluid":
                self.shell.send(f"set synth.gain {pct / 100:.3f}")
            else:
                cmd = build_volume_cmd(min(pct, 100), backend,
                                       self.vol_card, self.vol_control)
                if cmd:
                    try:
                        _sp.run(cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                                timeout=2)
                    except Exception as e:
                        print(f"volume backend failed: {e}", flush=True)
                else:
                    print("volume: no amixer/pactl found", flush=True)
            self._vol_last_pct = pct
            self._vol_last_t = now
        self.popup("Volume", f"{pct}%", v127)
        if self.ding_enabled:
            self._vol_ding_at = now
            self._vol_ding_armed = True

    def _blip(self, note: int, vel: int, ms: int):
        """Play a short test note on the synth + schedule its release."""
        if self.fs_out is None:
            return
        try:
            self.fs_out.send(self.mido.Message(
                "note_on", channel=0, note=note, velocity=vel))
            self._pending_offs.append((time.time() + ms / 1000, 0, note))
        except Exception:
            pass

    def _housekeeping(self):
        """Called every poll-loop tick: releases, idle volume ding."""
        now = time.time()
        if self._pending_offs and self.fs_out is not None:
            keep = []
            for deadline, ch, note in self._pending_offs:
                if deadline <= now:
                    try:
                        self.fs_out.send(self.mido.Message(
                            "note_off", channel=ch, note=note, velocity=0))
                    except Exception:
                        pass
                else:
                    keep.append((deadline, ch, note))
            self._pending_offs = keep
        if (self._vol_ding_armed and self.ding_enabled
                and now - self._vol_ding_at >= 0.35):
            self._vol_ding_armed = False
            self._blip(self.ding_note, self.ding_vel, self.ding_ms)
        self._click_tick()

    def _do_pan(self, v127: int):
        self.pan = v127
        if self.fs_out is not None:
            try:
                for ch in range(16):
                    self.fs_out.send(self.mido.Message(
                        "control_change", channel=ch, control=10, value=v127))
            except Exception:
                pass
        self.popup("Pan", pan_label(v127), v127)

    def _do_fx(self, name: str, value: int):
        """Apply an FX knob. value is 0..fx_top[name] (bass goes past 127)."""
        top = self.fx_top.get(name, 127)
        self.fx_val[name] = max(0, min(top, value))
        f01 = self.fx_val[name] / 127
        if name == "reverb":
            self.shell.reverb_level(f01)
        elif name == "room":
            self.shell.reverb_room(f01)
        elif name == "damp":
            self.shell.reverb_damp(f01)
        elif name == "chorus":
            self.shell.chorus_level(f01 * 4)
        elif name in ("bright", "attack", "release"):
            cc = {"bright": 74, "attack": 73, "release": 72}[name]
            if self.fs_out is not None:
                try:
                    for ch in range(16):
                        self.fs_out.send(self.mido.Message(
                            "control_change", channel=ch, control=cc,
                            value=min(127, self.fx_val[name])))
                except Exception:
                    pass
        # bass is app-side (sub-octave layers in extra_for), nothing to send
        self.popup(FX_TITLES[name], f"{fx_pct(self.fx_val[name])}%",
                   int(127 * self.fx_val[name] / top))

    def _sync_fx_from_synth(self):
        """Best-effort: align knob accumulators with fluidsynth state."""
        try:
            pairs = (("reverb", "synth.reverb.level", 1.0),
                     ("room", "synth.reverb.room-size", 1.0),
                     ("damp", "synth.reverb.damp", 1.0),
                     ("chorus", "synth.chorus.level", 4.0))
            for name, setting, full in pairs:
                if not self.fx_cc.get(name):
                    continue
                val = fluidmod.parse_float_reply(
                    self.shell.ask(f"info {setting}"))
                if val is not None:
                    self.fx_val[name] = max(0, min(127, round(val / full * 127)))
        except Exception:
            pass

    # -- display views -------------------------------------------------------
    # All browse modes walk the same (lib index) list machinery; `self.view()`
    # is what the current mode lets you scroll through.
    def view(self):
        """Library indices browsable in the current mode."""
        if self.mode == "favorites":
            return sorted(self.favs)
        if self.mode == "soundfonts":
            first = {}
            for i, p in enumerate(self.lib):
                first.setdefault(p["sfont"], i)
            return [first[k] for k in sorted(first)]
        return list(range(len(self.lib)))

    def view_total(self):
        return len(self.view())

    def _sync_highlight(self):
        """Point pending_idx at the loaded preset inside the current view."""
        if self.menu_open:
            return
        v = self.view()
        if not v:
            self.pending_idx = None
            return
        self.pending_idx = self.idx if self.idx in v else v[0]

    def show(self, p, pending=False):
        self.render(p, pending)

    def render(self, p, pending=False):
        total = self.view_total() or 1
        v = self.view()
        try:
            pos = v.index(p.get("_i", self.idx))
        except ValueError:
            pos = 0
        star = "*" if self.is_fav(p.get("_i", self.idx)) else " "
        mode_tag = {"instruments": "ALL", "favorites": "FAV",
                    "soundfonts": "SF"}.get(self.mode, self.mode[:3].upper())
        l1 = f"{p['sf']}"[:16]
        if pending:
            l2 = f">{star} {mode_tag} {p['name']}"[:28]
        else:
            l2 = f" {star} {mode_tag} {p['name']}"[:28]
        print(f"[{pos + 1}/{total}] {p['sf']} | "
              f"bank {p['bank']} prog {p['prog']} | {p['name']}" +
              (" (browsing)" if pending else ""), flush=True)
        if self.ml_out is None:
            return
        self._send(disp.msg_scroll(l1, l2, min(pos, 126), min(total, 127)))

    def _send(self, msg):
        """Push one display message (the MiniLab needs the init poke first)."""
        if self.ml_out is None:
            return
        try:
            self.ml_out.send(
                self.mido.Message("sysex",
                                  data=list(disp.msg_init()[1:-1])))
            self.ml_out.send(
                self.mido.Message("sysex", data=list(msg[1:-1])))
        except Exception as e:
            print(f"display send failed: {e}", flush=True)

    def show_menu(self):
        """Draw the mode menu (double-click the encoder to get here).

        Line 1 = highlighted mode, line 2 = position + what it does, and the
        scroll bar tracks position within the list so it reads as a real list.
        """
        keys = [k for k, _, _ in MODES]
        n = len(keys)
        self.menu_idx %= n
        key = keys[self.menu_idx]
        label = MODE_LABEL[key]
        print(f"menu {self.menu_idx + 1}/{n}: > {label} - {MODE_HELP[key]}",
              flush=True)
        l1 = f"{self.menu_idx + 1}/{n} {label}"[:16]
        l2 = MODE_HELP[key][:28]
        self._send(disp.msg_scroll(l1, l2, min(self.menu_idx, 126),
                                   min(n, 127)))

    def _menu_choose(self):
        """Activate the highlighted menu entry."""
        keys = [k for k, _, _ in MODES]
        key = keys[self.menu_idx % len(keys)]
        if key == "close":
            self._close_menu()
            return
        if key == "star":
            self._close_menu()
            self.toggle_fav(self.pending_idx if self.pending_idx is not None
                            else self.idx)
            return
        self.mode = key
        self._save_state()
        self._close_menu(after=True)
        if key == "soundfonts":
            print(f"mode: {MODE_LABEL[key]} - {MODE_HELP[key]}", flush=True)
        self._sync_highlight()
        self._render_current()

    def _open_menu(self):
        keys = [k for k, _, _ in MODES]
        if self.mode in keys:
            self.menu_idx = keys.index(self.mode)
        self._menu_saved = self.mode
        self.menu_open = True
        print("menu open (turn to choose, click to enter, Esc/close to leave)",
              flush=True)
        self.show_menu()

    def _close_menu(self, after=False):
        if not self.menu_open:
            return
        self.menu_open = False
        if not after and self._menu_saved:
            self.mode = self._menu_saved
        self._menu_saved = None
        self._sync_highlight()
        self._render_current()

    def _render_current(self):
        v = self.view()
        if not v:
            return
        i = self.pending_idx if self.pending_idx is not None else self.idx
        p = dict(self.lib[i])
        p["_i"] = i
        self.render(p, pending=self.pending_idx is not None)

    def _browse_step(self, delta: int):
        """Encoder/knob browsing: move highlight, never load (click confirms)."""
        v = self.view()
        if not v:
            return
        base = self.pending_idx if self.pending_idx is not None else self.idx
        if base not in v:
            base = v[0]
        self._browse_to(v[(v.index(base) + delta) % len(v)])

    def _browse_absolute(self, v127: int):
        v = self.view()
        if not v:
            return
        self._browse_to(v[min(len(v) - 1, int(v127 / 128 * len(v)))])

    def _menu_step(self, delta: int):
        self.menu_idx = (self.menu_idx + delta) % len(MODES)
        self.show_menu()

    def step(self, delta, quiet=False):
        if not self.lib:
            return
        self.apply((self.idx + delta) % len(self.lib), quiet=quiet)

    def step_soundfont(self, direction: int):
        """Jump to the first preset of the prev/next soundfont file."""
        if not self.lib:
            return
        sfonts = sorted({p["sfont"] for p in self.lib})
        if len(sfonts) < 2:
            self.popup("SoundFont", self.lib[self.idx]["sf"],
                       int(self.idx / max(1, len(self.lib)) * 127))
            return
        cur_sf = self.lib[self.idx]["sfont"]
        target = sfonts[(sfonts.index(cur_sf) + direction) % len(sfonts)]
        for j, p in enumerate(self.lib):
            if p["sfont"] == target:
                print(f"soundfont -> {p['file']}", flush=True)
                if self.mode == "soundfonts":  # stay in the soundfont list
                    self.idx = j
                    self.pending_idx = j
                    p2 = dict(p)
                    p2["_i"] = j
                    self.render(p2, pending=True)
                else:
                    self.apply(j)
                return

    def _encoder_delta(self, v: int):
        """Turn one encoder CC into a step direction, or None if it is a
        position instead.

        'auto' guesses: Arturia binary-offset relatives live at 60..68, so
        anything in that band is a step and everything else is a position.
        """
        m = self.encoder_mode
        if m == "absolute":
            return None
        if m.startswith("rel"):
            d = rel2_delta(v)
        elif 60 <= v <= 68:
            d = 0 if v == 64 else (1 if v > 64 else -1)
        else:
            return None
        return 1 if d > 0 else (-1 if d < 0 else 0)

    def _browse_encoder(self, v: int):
        """Main encoder below the display: highlight only, click loads.

        In volume mode it drives loudness instead; in the menu it scrolls.
        """
        if self.menu_open:
            d = self._encoder_delta(v)
            if d is None:
                self.menu_idx = min(len(MODES) - 1,
                                    int(v / 128 * len(MODES)))
                self.show_menu()
            elif d != 0:
                self._menu_step(d)
            return
        if self.mode == "volume":
            self._do_volume(v)
            return
        if not self.lib:
            return
        d = self._encoder_delta(v)
        if d is None:
            self._browse_absolute(v)
        elif d != 0:
            self._browse_step(d)

    def _preview_note(self, port, note: int, vel: int, ms: int):
        """Audition blip after a preset loads (worker thread)."""
        try:
            port.send(self.mido.Message(
                "note_on", channel=0, note=note, velocity=vel))
            time.sleep(max(0.05, ms / 1000))
            port.send(self.mido.Message(
                "note_off", channel=0, note=note, velocity=0))
        except Exception:
            pass

    def _ensure_worker_port(self):
        """Worker-local fluidsynth MIDI port (reopened if stale)."""
        try:
            names = [n for n in self.mido.get_output_names()
                     if "fluid" in n.lower() or "synth" in n.lower()]
            return self.mido.open_output(names[0]) if names else None
        except Exception:
            return None

    def _loader_loop(self):
        """Background: resolve path->sfont (loading big files on demand,
        coalesced to where the user landed) then select the preset."""
        fluid = fluidmod.FluidControl()
        fs = None
        while True:
            item = self._load_q.get()
            try:  # skip stale spins, load only where the user landed
                while True:
                    item = self._load_q.get_nowait()
            except queue.Empty:
                pass
            _, path, bank, prog = item
            sid = self._resolve_font(path)
            if sid is None:
                print(f"preset load failed for {os.path.basename(path)}",
                      flush=True)
                continue
            if fluid.select(sid, bank, prog, retries=2):
                loaded = True
            else:
                loaded = False
                try:  # shell down: MIDI fallback on a worker-local port
                    if fs is None:
                        fs = self._ensure_worker_port()
                    if fs is not None:
                        fs.send(self.mido.Message(
                            "control_change", channel=0, control=0,
                            value=(bank >> 7) & 0x7F))
                        fs.send(self.mido.Message(
                            "control_change", channel=0, control=32,
                            value=bank & 0x7F))
                        fs.send(self.mido.Message(
                            "program_change", channel=0, program=prog % 128))
                        loaded = True
                except Exception:
                    fs = None  # stale port (synth respawned?) -> reopen next time
            if loaded and self.pv_enabled:
                if fs is None:
                    fs = self._ensure_worker_port()
                if fs is not None:
                    try:
                        self._preview_note(fs, self.pv_note,
                                           self.pv_vel, self.pv_ms)
                    except Exception:
                        fs = None

    # -- message handling: returns True if consumed (don't forward) --
    def handle(self, msg):
        t = msg.type
        if t == "control_change":
            if msg.control == 0:
                self.bank_msb = msg.value
                return False
            if msg.control == 32:
                self.bank_lsb = msg.value
                return False
            if msg.control == self.vol_cc and self.vol_cc:
                self._do_volume(msg.value)
                return True
            if msg.control == self.pan_cc and self.pan_cc:
                self._do_pan(msg.value)
                return True
            for name in FX_DEFS:
                if msg.control == self.fx_cc.get(name):
                    self._do_fx(name, fx_accumulate(
                        self.fx_val[name], msg.value, self.knob_mode,
                        self.fx_top.get(name, 127)))
                    return True
            if msg.control == self.encoder_cc and self.encoder_cc:
                self._browse_encoder(msg.value)
                return True
            if msg.control == self.preset_cc:
                # preset knob browses too (highlight; click loads). While the
                # menu is open it scrolls the menu instead.
                if self.menu_open:
                    if self.knob_mode.startswith("rel"):
                        d = rel2_delta(msg.value)
                        if d != 0:
                            self._menu_step(1 if d > 0 else -1)
                    elif self.knob_mode == "absolute":
                        self.menu_idx = min(
                            len(MODES) - 1, int(msg.value / 128 * len(MODES)))
                        self.show_menu()
                    return True  # consume: don't send filter jumps to synth
                if self.knob_mode.startswith("rel"):
                    d = rel2_delta(msg.value)
                    if d != 0:
                        self._browse_step(1 if d > 0 else -1)
                else:  # absolute: map 0..127 across the current view
                    if self.view():
                        self._browse_absolute(msg.value)
                return True  # consume: don't send filter jumps to synth
            if self.click_cc and msg.control == self.click_cc:
                # 127 = pressed, 0 = released (Arturia button convention)
                if msg.value >= 64:
                    self._click_press()
                else:
                    self._click_release()
                return True
            return False
        if t == "program_change":
            bank = (self.bank_msb << 7) | self.bank_lsb
            # find preset in current sfont matching bank/prog
            cur = self.current()
            if cur:
                for i, p in enumerate(self.lib):
                    if (p["sfont"] == cur["sfont"] and p["bank"] == bank
                            and p["prog"] == msg.program):
                        self.apply(i)
                        break
                else:
                    if msg.program < len(self.lib):
                        self.apply(msg.program)
            return True
        if t in ("note_on", "note_off"):
            if self.click_note and msg.note == self.click_note:
                # pads-channel press/release pair -> click gestures
                if t == "note_on" and msg.velocity > 0:
                    self._click_press()
                elif t == "note_off" or msg.velocity == 0:
                    self._click_release()
                return True
            if t == "note_on" and msg.velocity > 0:
                if msg.note == self.prev_note:
                    self.step(-1)
                    return True
                if msg.note == self.next_note:
                    self.step(1)
                    return True
                if msg.note == self.sf_prev_note:
                    self.step_soundfont(-1)
                    return True
                if msg.note == self.sf_next_note:
                    self.step_soundfont(1)
                    return True
                if (msg.channel == self.pad_channel
                        and self.click_note == 0):
                    # unassigned pad: treat as encoder click gestures
                    self._click_press() if msg.velocity > 0 else None
                    return True
                # keys audition the highlight (loads it first) when enabled
                if (self.pending_idx is not None and self.confirm_on_note
                        and msg.channel == 0):
                    self._confirm("key press")
            return False
        return False

    def extra_for(self, msg):
        """Extra messages to inject alongside a forwarded one (bass booster).

        Returns a list (usually empty). Tracks (channel, note) -> sub notes
        so releases always match, even on retrigger or all-notes-off gaps.
        """
        out = []
        if msg.type == "note_on" and msg.velocity > 0:
            key = (msg.channel, msg.note)
            old = self._subs.pop(key, None) or []
            for n in old:
                out.append(self.mido.Message("note_off", channel=msg.channel,
                                             note=n, velocity=0))
            if not self.fx_cc.get("bass") or msg.note < 12:
                return out
            subs = bass_levels(self.fx_val.get("bass", 0), msg.velocity)
            made = []
            for shift, vel in subs:
                n = msg.note + shift
                if n < 0:
                    continue
                made.append(n)
                out.append(self.mido.Message("note_on", channel=msg.channel,
                                             note=n, velocity=vel))
            if made:
                self._subs[key] = made
        elif msg.type == "note_off" or (msg.type == "note_on" and
                                        msg.velocity == 0):
            key = (msg.channel, msg.note)
            old = self._subs.pop(key, None) or []
            for n in old:
                out.append(self.mido.Message("note_off", channel=msg.channel,
                                             note=n, velocity=0))
        return out

    def _close_ports(self):
        for attr in ("inport", "fs_out", "ml_out"):
            port = getattr(self, attr, None)
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass
                setattr(self, attr, None)

    def run(self):
        if not self.sf_files:
            print("soundfonts/ is empty, waiting 30s for upload then exit.",
                  flush=True)
            time.sleep(30)
            return
        loader_started = False
        fails = 0
        while True:  # survive unplug/replug + audio loss without systemd churn
            try:
                # (re)start the synth if needed (unplugged DAC, boot w/o audio)
                if self.proc is None or self.proc.poll() is not None:
                    self._reap_fluid()
                    if fails:
                        time.sleep(min(30, 5 * fails))
                    n_pre = len(getattr(self, "preload_files", self.sf_files))
                    print(f"starting fluidsynth ({n_pre} file(s) preloaded)...",
                          flush=True)
                    self.start_fluid()
                    time.sleep(3)
                    if self.proc is not None and self.proc.poll() is not None:
                        fails += 1
                        tail = self._fluid_log_tail()
                        hint = (" stale fluidsynth holding port 9800/audio? "
                                "kill it: pkill -f 'fluidsynth -is'."
                                if "error 98" in tail else "")
                        print(f"fluidsynth died on start ({tail}).{hint} "
                              "Retrying...", flush=True)
                        continue
                    fails = 0
                    self._reset_dyn()
                self._close_ports()
                self._subs = {}
                self.open_ports()  # waits forever; raises only if synth died
                self._sync_fx_from_synth()
                if not loader_started:
                    threading.Thread(target=self._loader_loop,
                                     daemon=True).start()
                    loader_started = True
                self.apply(self.idx)
                self._warm_current()
                self._sync_highlight()
                self._render_current()
                self.popup("sfbox ready", f"{len(self.lib)} presets",
                           min(127, len(self.lib)))
                if self.click_note or self.click_cc:
                    how = (f"pad (note {self.click_note})" if self.click_note
                           else f"CC {self.click_cc}")
                    print(f"  {how}: single = confirm, double = menu, "
                          "long = favourite", flush=True)
                else:
                    print("  NOTE: no click control mapped (click_note/"
                          "click_cc = 0), so confirm/menu/favourite "
                          "gestures are off.", flush=True)
                print("Ready. Scroll to browse, click to load. "
                      "Ctrl-C to stop.", flush=True)
                last_watch = time.time()
                last_enum = 0.0
                while True:  # non-blocking poll: unplug-proof + watchdog
                    try:
                        pending = self.inport.iter_pending()
                    except Exception as e:
                        print(f"MIDI port failed ({e}), reconnecting...",
                              flush=True)
                        break
                    for msg in pending:
                        try:
                            if not self.handle(msg):
                                if self.fs_out:
                                    self.fs_out.send(msg)
                                    for extra in self.extra_for(msg):
                                        self.fs_out.send(extra)
                        except Exception as e:
                            print(f"midi error: {e}", flush=True)
                    self._housekeeping()
                    now = time.time()
                    if now - last_watch > 2:
                        last_watch = now
                        if (self.proc is not None and
                                self.proc.poll() is not None):
                            print("fluidsynth died during play "
                                  f"({self._fluid_log_tail()}). "
                                  "Respawning...", flush=True)
                            break
                    if now - last_enum > 2:
                        last_enum = now
                        try:
                            present = (self._ml_in_name in
                                       self.mido.get_input_names())
                        except Exception:
                            present = True
                        if not present:
                            print("MiniLab unplugged, waiting for replug...",
                                  flush=True)
                            break
                    time.sleep(0.005)
                print("reconnecting in 2s...", flush=True)
                time.sleep(2)
            except RuntimeError as e:
                print(f"port error ({e}). Retrying in 5s...", flush=True)
                time.sleep(5)
            except KeyboardInterrupt:
                raise


def cmd_learn(config):
    import mido
    mido.set_backend("mido.backends.rtmidi")
    kws = cfg_get(config, "midi", "input_keywords", "minilab").split(",")
    for _ in range(60):
        ins, _, ml_in, _ = find_ports(mido, kws)
        if ml_in:
            break
        print("waiting for MiniLab... inputs:", ins, flush=True)
        time.sleep(1)
    else:
        print("MiniLab not found. inputs:", mido.get_input_names())
        return
    print(f"Learning on {ml_in}: twist knobs, hit pads. Ctrl-C to exit.",
          flush=True)
    with mido.open_input(ml_in) as p:
        for msg in p:
            print(msg, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--learn", action="store_true",
                    help="print incoming MIDI, no synth")
    ap.add_argument("--list", action="store_true",
                    help="list soundfonts/presets, no synth")
    ap.add_argument("--serve", type=int, default=0, metavar="PORT",
                    help="also run browser uploader on PORT (e.g. 8080)")
    args = ap.parse_args()
    config = load_config()

    if args.list:
        sf_dir = os.path.join(
            HERE, cfg_get(config, "fluid", "soundfont_dir", "soundfonts"))
        files = find_soundfonts(sf_dir)
        print(f"{len(files)} soundfont(s) in {sf_dir}")
        for f in files:
            ps = sf2mod.list_presets_sf2(f)
            print(f"  {os.path.basename(f)}: {len(ps)} presets")
            for b, pg, n in ps[:8]:
                print(f"    bank {b} prog {pg}: {n}")
        return

    if args.learn:
        cmd_learn(config)
        return

    sf_dir = os.path.join(
        HERE, cfg_get(config, "fluid", "soundfont_dir", "soundfonts"))
    os.makedirs(sf_dir, exist_ok=True)
    if args.serve:
        th = threading.Thread(target=serve_upload, args=(sf_dir, args.serve),
                              daemon=True)
        th.start()
        print(f"uploader on http://<pi-ip>:{args.serve}/", flush=True)

    Player(config).run()


if __name__ == "__main__":
    main()
