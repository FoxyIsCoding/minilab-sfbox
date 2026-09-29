#!/usr/bin/env python3
"""Self-tests for minilab-sfbox (run: python3 tests/test_basic.py). No hardware needed."""

import os
import struct
import time
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import minilab_display as disp
import sf2 as sf2mod
import fluid as fluidmod
from app import rel2_delta, build_library


def make_sf2(path, presets):
    """Write a minimal but structurally valid .sf2 with N presets."""
    # phdr: 38 bytes per record, last = EOP terminator
    phdr = b""
    for bank, prog, name in presets:
        phdr += name.encode("ascii")[:20].ljust(20, b"\x00")
        phdr += struct.pack("<HHHIII", prog, bank, 0, 0, 0, 0)
    phdr += b"\x00" * 20 + struct.pack("<HHHIII", 0xFFFF, 0xFFFF, 0, 0, 0, 0)
    # pdta LIST chunk: 'pdta' + phdr subchunk; add mandatory empty bag/gen lists
    pdta_inner = b"phdr" + struct.pack("<I", len(phdr)) + phdr
    for sid in (b"pbag", b"pmod", b"pgen", b"inst", b"ibag", b"imod",
                b"igen", b"shdr"):
        pdta_inner += sid + struct.pack("<I", 4) + b"\x00" * 4
    pdta = b"LIST" + struct.pack("<I", 4 + len(pdta_inner)) + b"pdta" + pdta_inner
    # minimal INFO + sdta so file looks like a real sfbk
    info = (b"LIST" + struct.pack("<I", 16) + b"INFO" +
            b"ifil" + struct.pack("<I", 4) + struct.pack("<HH", 2, 1))
    sdta = b"LIST" + struct.pack("<I", 12) + b"sdta" + b"smpl" + struct.pack("<I", 0)
    body = b"sfbk" + info + sdta + pdta
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", len(body)) + body)


fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (" " + str(extra) if extra and not cond else ""))
    if not cond:
        fails.append(name)


# 1. display messages are 7-bit safe SysEx with Arturia header
for fn, m in [("init", disp.msg_init()),
              ("text", disp.msg_text("Piano", "1/3 Grand", "heart", "play")),
              ("left", disp.msg_text_left("Hello", "World")),
              ("info", disp.msg_info("SF", "Piano", 64, "knob")),
              ("scroll", disp.msg_scroll("SF", "1/10 X", 0, 10)),
              ("pad", disp.msg_pad_color(0, 0x00, 0x7F, 0x00))]:
    check(f"sysex.{fn}.framing", m[0] == 0xF0 and m[-1] == 0xF7)
    check(f"sysex.{fn}.7bit", all(b <= 0x7F for b in m[1:-1]))
    check(f"sysex.{fn}.arturia", bytes(m[1:6]) == bytes([0x00, 0x20, 0x6B, 0x7F, 0x42]))

# 2. sf2 preset parsing (synthetic file, no samples loaded)
os.makedirs("/tmp/opencode/minilab-test", exist_ok=True)
p1 = "/tmp/opencode/minilab-test/t1.sf2"
make_sf2(p1, [(0, 0, "GrandPiano"), (0, 1, "BrightPiano"), (128, 0, "StdKit")])
got = sf2mod.list_presets_sf2(p1)
check("sf2.count", len(got) == 3, got)
check("sf2.names", got == [(0, 0, "GrandPiano"), (0, 1, "BrightPiano"), (128, 0, "StdKit")], got)
check("sf2.garbage", sf2mod.list_presets_sf2("/tmp/opencode/minilab-test/nope.sf2") == [])

# 3. library flattening: sfont ids follow file order
p2 = "/tmp/opencode/minilab-test/t2.sf2"
make_sf2(p2, [(0, 5, "Strings")])
lib = build_library([p1, p2])
check("lib.len", len(lib) == 4, len(lib))
check("lib.sfont", [e["sfont"] for e in lib] == [1, 1, 1, 2])
check("lib.order", lib[3]["name"] == "Strings" and lib[3]["prog"] == 5)

# 4. relative2 deltas (Arturia Relative #2, 2's complement)
check("rel.up1", rel2_delta(1) == 1)
check("rel.up3", rel2_delta(3) == 3)
check("rel.down1", rel2_delta(127) == -1)
check("rel.down2", rel2_delta(126) == -2)
check("rel.noop", rel2_delta(0) == 0 and rel2_delta(64) == 0)

# 5. fluidsynth command: headless, alsa, telnet shell, pi2-friendly
cmd = fluidmod.build_command(["a.sf2", "b.sf2"], "hw:Device", 0.8, 44100, 64)
s = " ".join(cmd)
for token in ["-is", "-a alsa", "-m alsa_seq", "shell.port",
              "synth.polyphony=64", "synth.cpu-cores=4", "a.sf2", "b.sf2"]:
    check(f"fluid.{token}", token in s, s)

# 6. handle(): knob/pads/program consume vs forward
sys.path.insert(0, os.path.join(HERE, ".."))
import configparser
from app import (Player, load_config, fx_accumulate, fx_pct, pan_label,
                 build_volume_cmd, fav_key, MODES)
import fluid as fluidmod2
c = configparser.ConfigParser()
c.read(os.path.join(HERE, "..", "config.ini"))

class FakeOut:
    def __init__(self): self.sent = []
    def send(self, m): self.sent.append(m)

import mido
p = Player.__new__(Player)
p.c = c
p.lib = [{"sfont": 1, "file": "x", "bank": 0, "prog": i,
          "sf": "t", "name": f"P{i}"} for i in range(8)]
p.idx = 0
p.bank_msb, p.bank_lsb = 0, 0
p.mido = mido
p.fs_out, p.ml_out = FakeOut(), None
p.fluid = fluidmod.FluidControl()
p.fluid.select = lambda *a, **k: True  # no daemon in test
p._save_state = lambda: None
p.knob_mode, p.preset_cc, p.prev_note, p.next_note, p.midi_ch = "absolute", 16, 36, 37, 0
p.sf_prev_note, p.sf_next_note = 42, 43
p.display_mode = "daw"
p.encoder_cc, p.encoder_mode = 28, "auto"
import queue as _queue
p._load_q = _queue.Queue(maxsize=8)
p._last_show, p._last_save = 0.0, 0.0
p.vol_cc, p.vol_backend, p.vol_card, p.vol_control = 30, "auto", "default", "Master"
p.vol_cooldown, p._vol_last_pct, p._vol_last_t = 0.0, -1, 0.0
p.pan_cc, p.pan = 33, 64
p.fx_cc = {"reverb": 71, "room": 72, "damp": 73, "chorus": 74,
           "bass": 75, "bright": 76, "attack": 77, "release": 78}
p.fx_val = {k: 64 for k in p.fx_cc}
p.fx_top = {k: 127 for k in p.fx_cc}
p.fx_top["bass"] = 254
p.fx_val["bass"] = 64
p.shell = fluidmod2.FluidShell()
p.shell.send = lambda line: True  # no daemon in test
p._subs = {}
p.pending_idx = None
p.click_note, p.click_cc = 0, 0
p.pad_channel = 9
p.click_single_ms, p.click_double_ms, p.click_long_ms = 350, 400, 800
p.mode, p.menu_open, p.menu_idx = "instruments", False, 0
p.favs = []
p._press = None
p._last_click_t = 0.0
p._menu_saved = None
p.confirm_on_note = True
p.pv_enabled, p.pv_note, p.pv_vel, p.pv_ms = True, 72, 70, 250
p.ding_enabled, p.ding_note, p.ding_vel, p.ding_ms = True, 84, 40, 150
p._pending_offs = []
p._vol_ding_at, p._vol_ding_armed = 0.0, False
# preset knob browses (highlight only, no load, no idx change)
check("handle.knob.consume", p.handle(mido.Message("control_change", control=16, value=64)) is True)
check("handle.knob.pending", p.pending_idx == 4 and p.idx == 0
      and p._load_q.qsize() == 0, (p.pending_idx, p.idx))  # 64/128*8 = 4
check("handle.next", p.handle(mido.Message("note_on", note=37, velocity=100)) is True and p.idx == 1)
check("handle.prev", p.handle(mido.Message("note_on", note=36, velocity=100)) is True and p.idx == 0)
check("handle.note.fwd", p.handle(mido.Message("note_on", note=60, velocity=100)) is False)
check("handle.cc.fwd", p.handle(mido.Message("control_change", control=64, value=127)) is False)
check("handle.prog", p.handle(mido.Message("program_change", program=2)) is True and p.idx == 2)

# 7. volume / pan / fx consume + helpers
import shutil as _shutil
_real_which = _shutil.which
_shutil.which = lambda name: f"/usr/bin/{name}"  # pretend both backends exist
try:
    check("vol.cmd.amixer", build_volume_cmd(50, "amixer", "default", "Master") ==
          ["amixer", "-q", "-c", "default", "sset", "Master", "50%"])
    check("vol.cmd.pactl", build_volume_cmd(75, "pactl") ==
          ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "75%"])
    check("vol.cmd.clamp", build_volume_cmd(999, "pactl")[3] == "100%")
finally:
    _shutil.which = _real_which
check("pan.label", pan_label(0) == "L100" and pan_label(64) == "Center"
      and pan_label(127) == "R100", (pan_label(0), pan_label(64), pan_label(127)))
check("fx.acc.abs", fx_accumulate(10, 100, "absolute") == 100)
check("fx.acc.rel+", fx_accumulate(60, 3, "relative2") == 63)
check("fx.acc.rel-", fx_accumulate(60, 126, "relative2") == 58)
check("fx.acc.clamp", fx_accumulate(126, 5, "relative2") == 127
      and fx_accumulate(1, 127, "relative2") == 0)

p2 = Player.__new__(Player)
for k, v in vars(p).items():
    setattr(p2, k, v)
p2.fs_out = FakeOut()
check("handle.vol.consume",
      p2.handle(mido.Message("control_change", control=30, value=100)) is True)
check("handle.pan.consume",
      p2.handle(mido.Message("control_change", control=33, value=20)) is True
      and p2.pan == 20)
pan_msgs = [m for m in p2.fs_out.sent
            if m.type == "control_change" and m.control == 10]
check("pan.allchan", len(pan_msgs) == 16 and pan_msgs[0].value == 20,
      len(pan_msgs))
n_before = p2.idx
check("handle.fx.consume",
      p2.handle(mido.Message("control_change", control=71, value=90)) is True
      and p2.fx_val["reverb"] == 90 and p2.idx == n_before)

# 8. bass booster layers (0-200%, second octave past 100%)
p3 = Player.__new__(Player)
for k, v in vars(p).items():
    setattr(p3, k, v)
p3.fs_out = FakeOut()
p3._subs = {}
check("bass.pct.100", fx_pct(127) == 100 and fx_pct(254) == 200)
check("bass.acc.top", fx_accumulate(0, 127, "absolute", 254) == 254)
p3.fx_val = dict(p3.fx_val, bass=127)  # 100% = one octave, full vel
on = mido.Message("note_on", channel=0, note=60, velocity=100)
check("bass.note.fwd", p3.handle(on) is False)
ex = p3.extra_for(on)
check("bass.sub.added", len(ex) == 1 and ex[0].note == 48
      and ex[0].velocity == 100, ex)
off = mido.Message("note_off", channel=0, note=60)
ex2 = p3.extra_for(off)
check("bass.sub.released", len(ex2) == 1 and ex2[0].note == 48
      and ex2[0].type == "note_off", ex2)
p3.fx_val["bass"] = 254  # 200% = two octaves
ex3 = p3.extra_for(mido.Message("note_on", channel=0, note=60, velocity=100))
check("bass.boost.2oct", [m.note for m in ex3 if m.type == "note_on"] == [48, 36]
      and ex3[0].velocity == 100 and ex3[1].velocity == 80,
      [(m.type, m.note, m.velocity) for m in ex3])
ex4 = p3.extra_for(mido.Message("note_off", channel=0, note=60))
check("bass.boost.release", sorted(m.note for m in ex4) == [36, 48]
      and all(m.type == "note_off" for m in ex4), ex4)
p3.fx_val["bass"] = 0
check("bass.off.quiet", p3.extra_for(
    mido.Message("note_on", channel=0, note=60, velocity=100)) == [])
check("bass.low.skip", p3.extra_for(
    mido.Message("note_on", channel=0, note=5, velocity=100)) == [])
# non-note messages must pass through untouched (no AttributeError)
for m in (mido.Message("control_change", control=64, value=127),
          mido.Message("pitchwheel", pitch=8000),
          mido.Message("sysex", data=[1, 2, 3]),
          mido.Message("aftertouch", value=40),
          mido.Message("polytouch", note=60, value=40)):
    check(f"extra.passthrough.{m.type}", p3.extra_for(m) == [], m)

# 9. encoder browsing = highlight only; click/note confirms + loads
p5 = Player.__new__(Player)
for k, v in vars(p).items():
    setattr(p5, k, v)
p5.fs_out = FakeOut()
p5._load_q = _queue.Queue(maxsize=8)
p5.idx = 4
p5.pending_idx = None
p5.click_note = 55
p5.mode, p5.menu_open, p5.menu_idx, p5.favs = "instruments", False, 0, []
p5._press, p5._last_click_t, p5._menu_saved = None, 0.0, None
p5._double_until = 0.0
p5.click_single_ms, p5.click_double_ms, p5.click_long_ms = 350, 400, 800
check("enc.rel.up", p5.handle(mido.Message("control_change", control=28, value=66)) is True
      and p5.pending_idx == 5 and p5.idx == 4 and p5._load_q.qsize() == 0,
      (p5.pending_idx, p5.idx))
check("enc.rel.down", p5.handle(mido.Message("control_change", control=28, value=61)) is True
      and p5.pending_idx == 4, p5.pending_idx)
check("enc.rel.center", p5.handle(mido.Message("control_change", control=28, value=64)) is True
      and p5.pending_idx == 4 and p5.idx == 4, p5.pending_idx)
check("enc.abs", p5.handle(mido.Message("control_change", control=28, value=100)) is True
      and p5.pending_idx == 6, p5.pending_idx)  # 100/128*8 = 6
# rapid spin: highlight advances, sound untouched, nothing queued
for _ in range(20):
    p5.handle(mido.Message("control_change", control=28, value=66))
check("enc.spin", p5.pending_idx == (6 + 20) % 8 and p5.idx == 4
      and p5._load_q.qsize() == 0, (p5.pending_idx, p5.idx))
# click = press then release: confirms, idx jumps, load queued
check("click.press", p5.handle(mido.Message("note_on", note=55, velocity=100)) is True
      and p5._press is not None and p5._load_q.qsize() == 0)
check("click.release", p5.handle(mido.Message("note_off", note=55)) is True
      and p5.idx == (6 + 20) % 8 and p5.pending_idx is None
      and p5._press is None and p5._load_q.qsize() == 1,
      (p5.idx, p5.pending_idx))
item = p5._load_q.get_nowait()
check("enc.click.item", item == (2, "x", 0, 2), item)
# keys audition the highlight when enabled...
p5._browse_to(7)
check("key.confirm", p5.handle(mido.Message("note_on", channel=0, note=60, velocity=100)) is False
      and p5.idx == 7 and p5.pending_idx is None, (p5.idx, p5.pending_idx))
# ...but not when disabled
p5.confirm_on_note = False
p5._browse_to(3)
check("key.noconfirm", p5.handle(mido.Message("note_on", channel=0, note=60, velocity=100)) is False
      and p5.idx == 7 and p5.pending_idx == 3, (p5.idx, p5.pending_idx))
# pads always load directly and clear the highlight
check("pad.clears", p5.handle(mido.Message("note_on", note=37, velocity=100)) is True
      and p5.idx == 0 and p5.pending_idx is None, (p5.idx, p5.pending_idx))

# 9b. encoder menu: double-click opens, turn moves, click enters, Esc closes
p6 = Player.__new__(Player)
for k, v in vars(p).items():
    setattr(p6, k, v)
p6.fs_out = FakeOut()
p6._load_q = _queue.Queue(maxsize=8)
p6.idx, p6.pending_idx = 3, None
p6.click_note, p6.click_cc = 55, 0
p6.mode, p6.menu_open, p6.menu_idx, p6.favs = "instruments", False, 0, []
p6._press, p6._last_click_t, p6._menu_saved = None, 0.0, None
p6._double_until = 0.0
p6.click_single_ms, p6.click_double_ms, p6.click_long_ms = 350, 400, 800
keys = [k for k, _, _ in MODES]


def click(pl, note=55):
    """One deliberate encoder click: press + release, well clear of any
    previous gesture so it is not read as part of a double-click."""
    pl._last_click_t = 0.0
    pl._double_until = 0.0
    pl.handle(mido.Message("note_on", note=note, velocity=100))
    pl.handle(mido.Message("note_off", note=note))


def dblclick(pl, note=55):
    """Two clicks inside click_double_ms -> menu toggle."""
    pl._last_click_t = 0.0
    pl._double_until = 0.0
    pl.handle(mido.Message("note_on", note=note, velocity=100))
    pl.handle(mido.Message("note_off", note=note))
    pl.handle(mido.Message("note_on", note=note, velocity=100))
    pl.handle(mido.Message("note_off", note=note))


click(p6)
check("menu.firstclick.load", not p6.menu_open and p6.idx == 3, (p6.menu_open, p6.idx))
dblclick(p6)
check("menu.double.opens", p6.menu_open and p6.mode == "instruments", p6.menu_open)
p6.handle(mido.Message("control_change", control=28, value=66))
check("menu.scroll", p6.menu_idx == 1 and keys[p6.menu_idx] == "soundfonts", p6.menu_idx)
p6.handle(mido.Message("control_change", control=28, value=66))
p6.handle(mido.Message("control_change", control=28, value=66))
p6.handle(mido.Message("control_change", control=28, value=66))
check("menu.scroll2", keys[p6.menu_idx] == "volume", keys[p6.menu_idx])
# browsing while the menu is open must not move the preset highlight
before = p6.pending_idx
check("menu.no.load", p6.pending_idx == before and p6.idx == 3
      and p6._load_q.qsize() == 0, (p6.pending_idx, p6.idx))
# press+release selects the highlighted mode
click(p6)
check("menu.enter.volume", not p6.menu_open and p6.mode == "volume"
      and p6.menu_idx == keys.index("volume"), (p6.mode, p6.menu_idx))
# in volume mode the encoder is the fader
p6.vol_cc, p6.vol_backend = 14, "fluid"
seen_gain = []
p6.shell.send = lambda line: seen_gain.append(line) or True
p6._vol_last_pct, p6._vol_last_t, p6.vol_cooldown = -1, 0.0, 0.0
p6.ding_enabled = False
p6.handle(mido.Message("control_change", control=28, value=127))
check("menu.volume.knob", any("synth.gain" in g for g in seen_gain), seen_gain)
check("menu.volume.nobrowse", p6._load_q.qsize() == 0)
# the menu reopens on the current mode, wraps around, and 'close' restores it
dblclick(p6)
check("menu.reopen.atmode", p6.menu_open and keys[p6.menu_idx] == "volume",
      (p6.menu_open, keys[p6.menu_idx]))
p6.handle(mido.Message("control_change", control=28, value=66))
check("menu.scroll.close", keys[p6.menu_idx] == "close", keys[p6.menu_idx])
click(p6)
# 'close' is a pure escape hatch: it returns to whatever mode you were in
check("menu.close.restores", not p6.menu_open and p6.mode == "volume", p6.mode)
p6.mode = "instruments"
p6._sync_highlight()

# 9b2. the real MiniLab encoder press: CC 118, 127 down / 0 up
p6.click_note, p6.click_cc = 0, 118
p6.idx, p6.pending_idx, p6.favs = 3, 3, []
p6._last_click_t = 0.0  # nothing in flight from the previous block
while not p6._load_q.empty():
    p6._load_q.get_nowait()
down = mido.Message("control_change", control=118, value=127)
up = mido.Message("control_change", control=118, value=0)
check("cc118.down.consumed", p6.handle(down) is True and p6._press is not None)
check("cc118.hold.nofire", p6._click_tick() is None and p6.favs == [])
check("cc118.up.confirms", p6.handle(up) is True and p6._press is None
      and p6._load_q.qsize() == 1 and not p6.menu_open, p6._load_q.qsize())
while not p6._load_q.empty():
    p6._load_q.get_nowait()
# two quick presses = the menu (the trailing release must not re-trigger)
p6._double_until = 0.0
p6.handle(down)
p6.handle(up)
p6.handle(down)
p6.handle(up)
check("cc118.double.menu", p6.menu_open, p6.menu_open)
# the 4-message double must not leave a phantom press behind, or the next
# real click would be read as a double
check("cc118.double.nopress", p6._press is None, p6._press)
# a second press inside the window (press not yet released) also counts
p6._double_until = 0.0
p6.handle(down)
p6.handle(down)
check("cc118.double.overlap", not p6.menu_open, p6.menu_open)
check("cc118.overlap.nopress", p6._press is None, p6._press)
# ...and the click after that is a single click, not another double
p6._double_until = 0.0
p6.handle(down)
p6.handle(up)
check("cc118.after.double.is.single", p6._load_q.qsize() == 1
      and not p6.menu_open, (p6._load_q.qsize(), p6.menu_open))
while not p6._load_q.empty():
    p6._load_q.get_nowait()
# hold past click_long_ms = favourite, and the release must not also confirm
p6._double_until = 0.0
p6._browse_to(1)
p6.handle(down)
p6._press["t"] -= 2.0
p6._click_tick()
check("cc118.long.fav", p6.favs == [1] and p6._press is None, p6.favs)
p6.handle(up)
check("cc118.long.noconfirm", p6._load_q.qsize() == 0
      and p6.favs == [1], (p6._load_q.qsize(), p6.favs))
check("cc118.nofwd", p6.handle(mido.Message("control_change", control=118,
                                           value=64)) is True)
p6.click_cc = 0

# 9c. favourites: long press stars, favourites mode only walks starred presets
p7 = Player.__new__(Player)
for k, v in vars(p).items():
    setattr(p7, k, v)
p7.fs_out = FakeOut()
p7._load_q = _queue.Queue(maxsize=8)
p7.idx, p7.pending_idx = 2, None
p7.click_note, p7.click_cc = 55, 0
p7.mode, p7.menu_open, p7.menu_idx, p7.favs = "instruments", False, 0, []
p7._press, p7._last_click_t, p7._menu_saved = None, 0.0, None
p7._double_until = 0.0
p7.click_single_ms, p7.click_double_ms, p7.click_long_ms = 350, 400, 800
check("fav.key", fav_key(p7.lib[2]) == "x|0|2", fav_key(p7.lib[2]))
p7._browse_to(5)
p7.handle(mido.Message("note_on", note=55, velocity=100))
p7._press["t"] -= 2.0  # held past click_long_ms
p7._click_tick()
check("fav.longpress", p7.favs == [5] and p7._press is None, p7.favs)
p7._press = None
p7._click_tick()
check("fav.once", p7.favs == [5], p7.favs)
p7._press = None
p7._press = {"t": time.time() - 2.0, "long": False}
p7._click_tick()
check("fav.longpress.off", p7.favs == [], p7.favs)
# a quick click must not toggle a favourite
p7._browse_to(6)
click(p7)
check("fav.short.safe", p7.favs == [] and p7._load_q.qsize() == 1, p7.favs)
while not p7._load_q.empty():  # drain so the next count is exact
    p7._load_q.get_nowait()
# favourites mode browses only the starred list
p7.favs = [1, 4, 6]
p7.mode = "favorites"
p7._sync_highlight()
check("fav.view", p7.view() == [1, 4, 6] and p7.pending_idx == 6,
      (p7.view(), p7.pending_idx))
p7._browse_step(1)
check("fav.view.step", p7.pending_idx == 1, p7.pending_idx)
p7._browse_step(2)
check("fav.view.wrap", p7.pending_idx == 6, p7.pending_idx)
p7._browse_absolute(0)
check("fav.view.abs", p7.pending_idx == 1, p7.pending_idx)
# an unstarred current preset lands the highlight on the first starred one
p7.idx = 2
p7._sync_highlight()
check("fav.view.fallback", p7.pending_idx == 1, p7.pending_idx)
# picking a soundfont in soundfonts mode jumps there and leaves that mode
for i, pr in enumerate(p7.lib):
    pr["file"] = "a.sf2" if i < 5 else "b.sf2"
    pr["sfont"] = 1 if i < 5 else 2
p7.mode = "soundfonts"
p7.idx = 2
p7._sync_highlight()
check("sf.view", p7.view() == [0, 5], p7.view())
p7._browse_to(5)
click(p7)
check("sf.jump", p7.idx == 5 and p7.mode == "instruments"
      and p7._load_q.qsize() == 1, (p7.idx, p7.mode))
# state round-trip: favourites survive a restart, keyed by name not index
p8 = Player.__new__(Player)
for k, v in vars(p).items():
    setattr(p8, k, v)
p8.favs = [1, 4, 6]
keys_saved = [fav_key(p8.lib[i]) for i in p8.favs]
p8.favs = []
p8._restore_favs(keys_saved)
check("fav.roundtrip", p8.favs == [1, 4, 6], p8.favs)
p8._restore_favs(["nope.sf2|1|2"])
check("fav.roundtrip.miss", p8.favs == [], p8.favs)

# 10. shell effect command formatting
seen = []
p4shell = fluidmod2.FluidShell()
p4shell.send = lambda line: seen.append(line) or True
p4shell.reverb_level(0.5)
p4shell.reverb_room(0.25)
p4shell.reverb_damp(1.0)
p4shell.chorus_level(2.0)
check("shell.cmds", seen == ["set synth.reverb.level 0.500",
                             "set synth.reverb.room-size 0.250",
                             "set synth.reverb.damp 1.000",
                             "set synth.chorus.level 2.000"], seen)
check("shell.parse", fluidmod2.parse_float_reply("roomsize: 0.200\n> ") == 0.2
      and fluidmod2.parse_float_reply("no numbers here") is None)

# 11. reconnect helper closes ports and tolerates Nones
class FakePort:
    def __init__(self): self.closed = False
    def close(self): self.closed = True
p6 = Player.__new__(Player)
p6.inport, p6.fs_out, p6.ml_out = FakePort(), None, FakePort()
p6._close_ports()
check("close.ports", p6.inport is None and p6.fs_out is None
      and p6.ml_out is None)

# 12. volume fader -> fluid gain 0..125%, soundfont jump pads
p7 = Player.__new__(Player)
for k, v in vars(p).items():
    setattr(p7, k, v)
p7.fs_out = FakeOut()
p7._load_q = _queue.Queue(maxsize=8)
p7.vol_backend = "fluid"
p7.vol_cooldown, p7._vol_last_pct, p7._vol_last_t = 0.0, -1, 0.0
gsent = []
p7.shell = fluidmod2.FluidShell()
p7.shell.send = lambda line: gsent.append(line) or True
check("vol.fluid.consume",
      p7.handle(mido.Message("control_change", control=30, value=127)) is True
      and gsent == ["set synth.gain 1.250"], gsent)
gsent.clear()
check("vol.fluid.mid",
      p7.handle(mido.Message("control_change", control=30, value=64)) is True
      and gsent == ["set synth.gain 0.630"], gsent)

p8 = Player.__new__(Player)
for k, v in vars(p).items():
    setattr(p8, k, v)
p8.fs_out = FakeOut()
p8._load_q = _queue.Queue(maxsize=8)
p8.lib = ([{"sfont": 1, "file": "a.sf2", "bank": 0, "prog": i,
            "sf": "a", "name": f"A{i}"} for i in range(3)] +
          [{"sfont": 2, "file": "b.sf2", "bank": 0, "prog": i,
            "sf": "b", "name": f"B{i}"} for i in range(2)])
p8.idx = 1
check("sf.next", p8.handle(mido.Message("note_on", note=43, velocity=100)) is True
      and p8.idx == 3 and p8.lib[p8.idx]["sf"] == "b", p8.idx)
check("sf.next.wrap", p8.handle(mido.Message("note_on", note=43, velocity=100)) is True
      and p8.idx == 0, p8.idx)
check("sf.prev", p8.handle(mido.Message("note_on", note=42, velocity=100)) is True
      and p8.idx == 3, p8.idx)

# 13. lazy soundfont loading: partition, load-id parsing, LRU eviction
from app import partition_files, choose_evictions
check("part.split", partition_files([("a", 1), ("b", 9 * 1024 * 1024)], 8) ==
      (["a"], ["b"]))
check("part.edge", partition_files([("a", 8 * 1024 * 1024)], 8) == (["a"], []))
check("evict.basic", choose_evictions([("a", 100), ("b", 100)], "b", 150) == ["a"])
check("evict.keepcur", choose_evictions([("a", 100), ("b", 100)], "a", 50) == ["b"])
check("evict.fit", choose_evictions([("a", 100)], "a", 200) == [])
check("parse.loadid", fluidmod2.parse_load_id(
    "loaded SoundFont has ID 3 and bankofs=0\n") == 3
    and fluidmod2.parse_load_id("something failed") is None)
check("parse.fonts", fluidmod2.parse_fonts_list(
    "ID  Name\n 1  /x/gm.sf2\n 3  /y/my drums.sf2\n> ") ==
    {1: "/x/gm.sf2", 3: "/y/my drums.sf2"})


class StubShell:
    def __init__(self):
        self.next_id = 10
        self.unloaded = []

    def load_font(self, path):
        self.next_id += 1
        return self.next_id

    def unload_font(self, sid):
        self.unloaded.append(sid)


os.makedirs("/tmp/opencode/lazy-test", exist_ok=True)
small_p = "/tmp/opencode/lazy-test/small.sf2"
big1_p = "/tmp/opencode/lazy-test/big1.sf2"
big2_p = "/tmp/opencode/lazy-test/big2.sf2"
for p_, n in ((small_p, 100), (big1_p, 200), (big2_p, 200)):
    with open(p_, "wb") as f:
        f.write(b"\0" * n)

p9 = Player.__new__(Player)
for k, v in vars(p).items():
    setattr(p9, k, v)
p9.lib = [{"sfont": 1, "file": small_p, "bank": 0, "prog": 0,
           "sf": "small", "name": "S"},
          {"sfont": 2, "file": big1_p, "bank": 0, "prog": 0,
           "sf": "big1", "name": "B1"},
          {"sfont": 3, "file": big2_p, "bank": 0, "prog": 0,
           "sf": "big2", "name": "B2"}]
p9.idx = 0
p9.preload_ids = {small_p: 1}
p9.dyn_ids, p9.dyn_order, p9.dyn_bytes = {}, [], 0
p9.mem_cap = 300  # big1+big2 (400B) won't both fit
import threading as _th
p9._font_lock = _th.Lock()
p9._font_gen = 0
p9.shell = StubShell()
check("resolve.preload", p9._resolve_font(small_p) == 1)
check("resolve.load", p9._resolve_font(big1_p) == 11
      and p9.dyn_bytes == 200, (p9.dyn_ids, p9.dyn_bytes))
p9.idx = 2  # current = big2 so big1 becomes evictable
check("resolve.evict", p9._resolve_font(big2_p) == 12
      and p9.shell.unloaded == [11] and p9.dyn_bytes == 200,
      (p9.shell.unloaded, p9.dyn_bytes))
p9._reset_dyn()
check("reset.gen", p9.dyn_ids == {} and p9._font_gen == 1)
p9._load_q = _queue.Queue(maxsize=8)
p9.apply(1)
item = p9._load_q.get_nowait()
check("enqueue.path", item == (1, big1_p, 0, 0), item)

# 14. volume idle ding + audition blip plumbing
p10 = Player.__new__(Player)
for k, v in vars(p).items():
    setattr(p10, k, v)
p10.fs_out = FakeOut()
p10._load_q = _queue.Queue(maxsize=8)
p10.vol_backend = "fluid"
p10.vol_cooldown, p10._vol_last_pct, p10._vol_last_t = 0.0, -1, 0.0
p10.shell = fluidmod2.FluidShell()
p10.shell.send = lambda line: True
p10.handle(mido.Message("control_change", control=30, value=100))
check("ding.armed", p10._vol_ding_armed is True)
p10._vol_ding_at = time.time() - 1.0  # idle long enough
p10._housekeeping()
ons = [m for m in p10.fs_out.sent if m.type == "note_on"]
check("ding.fired", len(ons) == 1 and ons[0].note == 84
      and ons[0].velocity == 40 and len(p10._pending_offs) == 1, ons)
check("ding.once", (p10._housekeeping(), len(
    [m for m in p10.fs_out.sent if m.type == "note_on"]))[1] == 1)
p10._pending_offs = [(time.time() - 1.0, 0, 84)]  # release due
p10._housekeeping()
offs = [m for m in p10.fs_out.sent if m.type == "note_off"]
check("ding.released", len(offs) == 1 and offs[0].note == 84
      and p10._pending_offs == [], offs)
fout = FakeOut()
p10._preview_note(fout, 72, 70, 1)
check("preview.blip", [m.type for m in fout.sent] == ["note_on", "note_off"]
      and fout.sent[0].note == 72, [m.type for m in fout.sent])

print(f"\n{len(fails)} failure(s): {fails}" if fails else "\nALL TESTS PASSED")
sys.exit(1 if fails else 0)
