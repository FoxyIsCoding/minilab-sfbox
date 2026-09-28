"""Minimal SF2/SF3 preset-name reader (no deps, low RAM).

Parses RIFF chunks, finds pdta/phdr, returns [(bank, program, name)].
Only header bytes are read; sample data is skipped via seek.
"""

import os
import struct

MAX_PRESETS = 512


def list_presets_sf2(path: str, limit: int = MAX_PRESETS):
    presets = []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            riff = f.read(12)
            if len(riff) < 12 or riff[0:4] != b"RIFF" or riff[8:12] != b"sfbk":
                return []
            while f.tell() < size:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                ckid, cksz = hdr[0:4], struct.unpack("<I", hdr[4:8])[0]
                pos = f.tell()
                if ckid == b"LIST":
                    ltype = f.read(4)
                    end = pos + cksz + (cksz & 1)
                    if ltype == b"pdta":
                        while f.tell() + 8 <= pos + cksz:
                            shdr = f.read(8)
                            if len(shdr) < 8:
                                break
                            sid, ssz = (shdr[0:4],
                                        struct.unpack("<I", shdr[4:8])[0])
                            spos = f.tell()
                            if sid == b"phdr" and ssz >= 76:
                                n = ssz // 38
                                raw = f.read(n * 38)
                                for i in range(n):
                                    e = raw[i * 38:(i + 1) * 38]
                                    name = e[0:20].split(b"\x00")[0].decode(
                                        "ascii", errors="replace")
                                    prog, bank = struct.unpack("<HH", e[20:24])
                                    if i < n - 1:  # last = EOP terminator
                                        presets.append((bank, prog,
                                                        name.strip() or
                                                        f"Preset {prog}"))
                                        if len(presets) >= limit:
                                            return presets
                            f.seek(spos + ssz + (ssz & 1))
                    f.seek(end)
                else:
                    # skip chunk (with pad byte), avoid loading samples
                    f.seek(pos + cksz + (cksz & 1))
    except OSError:
        return []
    return presets
