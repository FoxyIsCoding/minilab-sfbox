#!/usr/bin/env python3
"""Timestamped MIDI sniffer for mapping MiniLab controls to CC numbers.

Usage: python3 sniff.py [seconds]   (logs to --log file, default stdout)
Move ONE control at a time, in a known order, then match by order of
first appearance in the log.
"""
import argparse
import sys
import time

import mido


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seconds", nargs="?", type=int, default=120)
    ap.add_argument("--log", default="-")
    ap.add_argument("--keyword", default="minilab")
    args = ap.parse_args()

    mido.set_backend("mido.backends.rtmidi")
    names = mido.get_input_names()
    pick = None
    for n in names:
        if args.keyword.lower() in n.lower() and "midi" in n.lower():
            pick = n
            break
    if pick is None:
        for n in names:
            if args.keyword.lower() in n.lower():
                pick = n
                break
    if pick is None:
        print(f"no input matching {args.keyword!r}; have: {names}")
        sys.exit(1)

    out = open(args.log, "w") if args.log != "-" else sys.stdout
    t0 = time.time()
    print(f"# sniffing {pick} for {args.seconds}s", file=out, flush=True)
    with mido.open_input(pick) as port:
        while time.time() - t0 < args.seconds:
            for msg in port.iter_pending():
                print(f"{time.time() - t0:7.2f} {msg}", file=out, flush=True)
            time.sleep(0.005)
    print("# done", file=out, flush=True)


if __name__ == "__main__":
    main()
