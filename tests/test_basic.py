#!/usr/bin/env python3
"""Self-tests for minilab-sfbox (run: python3 tests/test_basic.py). No hardware needed."""

import os
import struct
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
from app import (Player, load_config, fx_accumulate, pan_label,
                 build_volume_cmd)
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
p.shell = fluidmod2.FluidShell()
p.shell.send = lambda line: True  # no daemon in test
p._subs = {}
check("handle.knob.consume", p.handle(mido.Message("control_change", control=16, value=64)) is True)
check("handle.knob.maps", p.idx == 4, p.idx)  # 64/128*8 = 4
check("handle.next", p.handle(mido.Message("note_on", note=37, velocity=100)) is True and p.idx == 5)
check("handle.prev", p.handle(mido.Message("note_on", note=36, velocity=100)) is True and p.idx == 4)
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

# 8. sub-bass layer injection
p3 = Player.__new__(Player)
for k, v in vars(p).items():
    setattr(p3, k, v)
p3.fs_out = FakeOut()
p3._subs = {}
p3.fx_val = dict(p3.fx_val, bass=127)
on = mido.Message("note_on", channel=0, note=60, velocity=100)
check("bass.note.fwd", p3.handle(on) is False)
ex = p3.extra_for(on)
check("bass.sub.added", len(ex) == 1 and ex[0].note == 48
      and ex[0].velocity == 100, ex)
off = mido.Message("note_off", channel=0, note=60)
ex2 = p3.extra_for(off)
check("bass.sub.released", len(ex2) == 1 and ex2[0].note == 48
      and ex2[0].type == "note_off", ex2)
p3.fx_val["bass"] = 0
check("bass.off.quiet", p3.extra_for(
    mido.Message("note_on", channel=0, note=60, velocity=100)) == [])
check("bass.low.skip", p3.extra_for(
    mido.Message("note_on", channel=0, note=5, velocity=100)) == [])

# 9. main encoder browsing (relative binary-offset + absolute fallback)
p5 = Player.__new__(Player)
for k, v in vars(p).items():
    setattr(p5, k, v)
p5.fs_out = FakeOut()
p5._load_q = _queue.Queue(maxsize=8)
p5.idx = 4
check("enc.rel.up", p5.handle(mido.Message("control_change", control=28, value=66)) is True
      and p5.idx == 5, p5.idx)
check("enc.rel.down", p5.handle(mido.Message("control_change", control=28, value=61)) is True
      and p5.idx == 4, p5.idx)
check("enc.rel.center", p5.handle(mido.Message("control_change", control=28, value=64)) is True
      and p5.idx == 4, p5.idx)
check("enc.abs", p5.handle(mido.Message("control_change", control=28, value=100)) is True
      and p5.idx == 6, p5.idx)  # 100/128*8 = 6
# rapid spin: every tick advances idx, queue coalesces pending loads
for _ in range(20):
    p5.handle(mido.Message("control_change", control=28, value=66))
check("enc.spin", p5.idx == (6 + 20) % 8 and p5._load_q.qsize() <= 8, (p5.idx, p5._load_q.qsize()))

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

print(f"\n{len(fails)} failure(s): {fails}" if fails else "\nALL TESTS PASSED")
sys.exit(1 if fails else 0)
