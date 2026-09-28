"""SysEx helpers for Arturia MiniLab 3 display + pad colors.

Reverse-engineered protocol, see:
  https://gist.github.com/Janiczek/04a87c2534b9d1435a1d8159c742d260
Only full 7-bit-safe messages are built here (F0 ... F7 included).

Display messages only take effect reliably in DAW mode. If nothing shows,
switch the MiniLab to DAW program (Shift + Pad 8 on most firmware) and retry.
Script works fine without display - control still applies.
"""

ARTURIA = bytes([0x00, 0x20, 0x6B, 0x7F, 0x42])

INIT_MSG = bytes([0xF0]) + ARTURIA + bytes([0x02, 0x02, 0x40, 0x6A, 0x21, 0xF7])

PICTURES = {"none": 0x00, "heart": 0x01, "play": 0x02, "rec": 0x03,
            "armed": 0x04, "shift": 0x05}
CONTROLS = {"knob": 0x03, "fader": 0x04, "pad": 0x05, "scroll": 0x06}


def _ascii(s: str, maxlen: int = 28) -> bytes:
    b = s.encode("ascii", errors="replace").replace(b"?", b" ")
    return b[:maxlen]


def msg_init() -> bytes:
    return INIT_MSG


def msg_text(line1: str, line2: str,
             pic1: str = "none", pic2: str = "none") -> bytes:
    """Centered 2-line text with optional pictograms."""
    p1, p2 = PICTURES[pic1], PICTURES[pic2]
    return (bytes([0xF0]) + ARTURIA + bytes([0x04, 0x02, 0x60,
            0x1F, 0x07, 0x01, p1, p2, 0x01, 0x00,
            0x01]) + _ascii(line1) + bytes([0x00, 0x02]) +
            _ascii(line2) + bytes([0x00, 0xF7]))


def msg_text_left(line1: str, line2: str) -> bytes:
    return (bytes([0xF0]) + ARTURIA + bytes([0x04, 0x02, 0x60, 0x01]) +
            _ascii(line1) + bytes([0x00, 0x02]) + _ascii(line2) +
            bytes([0xF7]))


def msg_info(line1: str, line2: str, value: int,
             control: str = "knob", autohide: bool = False) -> bytes:
    value = max(0, min(127, value))
    ah = 0x02 if autohide else 0x00
    cc = CONTROLS[control]
    return (bytes([0xF0]) + ARTURIA + bytes([0x04, 0x02, 0x60,
            0x1F, cc, ah, value, 0x00, 0x00, 0x01]) + _ascii(line1) +
            bytes([0x00, 0x02]) + _ascii(line2) + bytes([0xF7]))


def msg_scroll(line1: str, line2: str, pos: int, length: int,
               autohide: bool = False) -> bytes:
    pos = max(0, min(127, pos))
    length = max(1, min(127, length))
    ah = 0x02 if autohide else 0x00
    return (bytes([0xF0]) + ARTURIA + bytes([0x04, 0x02, 0x60,
            0x1F, 0x06, ah, pos, 0x00, length, 0x00, 0x00, 0x01]) +
            _ascii(line1) + bytes([0x00, 0x02]) + _ascii(line2) +
            bytes([0x00, 0xF7]))


def msg_pad_color(pad_index_0_7: int, r: int, g: int, b: int,
                  mode: str = "daw") -> bytes:
    """Set pad/bank-A color. mode: daw|arturia|user. r/g/b 0..127."""
    prefix = {"daw": (0x02, 0x02), "arturia": (0x02, 0x01),
              "user": (0x02, 0x00)}[mode]
    pid = 0x04 + pad_index_0_7
    r, g, b = (max(0, min(0x7F, v)) for v in (r, g, b))
    return (bytes([0xF0]) + ARTURIA +
            bytes([prefix[0], prefix[1], 0x16, pid, r, g, b, 0xF7]))
