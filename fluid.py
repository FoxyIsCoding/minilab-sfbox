"""FluidSynth process management + preset selection."""

import shutil
import socket
import subprocess
import threading
import time

SHELL_PORT = 9800


def build_command(sf_files, audio_device="default", gain=0.8,
                  samplerate=44100, polyphony=64):
    exe = shutil.which("fluidsynth") or "fluidsynth"
    return [exe, "-is",
            "-a", "alsa",
            "-o", f"audio.alsa.device={audio_device}",
            "-o", "audio.period-size=128",
            "-o", "audio.periods=3",
            "-m", "alsa_seq",
            "-r", str(samplerate),
            "-g", str(gain),
            "-o", f"synth.polyphony={polyphony}",
            "-o", "synth.cpu-cores=4",
            "-o", f"shell.port={SHELL_PORT}",
            ] + list(sf_files)


class FluidControl:
    """Select presets via fluidsynth telnet shell, MIDI fallback."""

    def __init__(self, port=SHELL_PORT):
        self.port = port

    def select(self, sfont: int, bank: int, prog: int,
               chan: int = 0, retries: int = 8) -> bool:
        cmd = f"select {chan} {sfont} {bank} {prog}\n".encode()
        for _ in range(retries):
            try:
                with socket.create_connection(("127.0.0.1", self.port),
                                              timeout=1) as s:
                    s.settimeout(1.5)
                    try:
                        s.recv(4096)  # banner/prompt
                    except socket.timeout:
                        pass
                    s.sendall(cmd)
                    time.sleep(0.05)
                    return True
            except OSError:
                time.sleep(0.5)
        return False


class FluidShell:
    """Persistent TCP connection for knob-rate effect commands.

    Fire-and-forget `send()` for sweeps (with opportunistic drain so the
    server's reply buffer can't grow), blocking `ask()` for startup sync.
    """

    def __init__(self, port=SHELL_PORT):
        self.port = port
        self.sock = None
        self.lock = threading.Lock()

    def _ensure(self) -> bool:
        if self.sock is not None:
            return True
        for _ in range(3):
            try:
                s = socket.create_connection(("127.0.0.1", self.port),
                                             timeout=2)
                s.settimeout(0.6)
                self.sock = s
                return True
            except OSError:
                time.sleep(0.4)
        return False

    def _drop(self):
        try:
            if self.sock is not None:
                self.sock.close()
        except OSError:
            pass
        self.sock = None

    def send(self, line: str) -> bool:
        """Send one shell command without ever blocking the MIDI loop.

        `set` commands produce no reply, so any blocking read here would
        stall note forwarding for the full socket timeout on every knob
        tick. Drain opportunistically with a zero timeout instead.
        """
        with self.lock:
            if not self._ensure():
                return False
            try:
                self.sock.sendall((line + "\n").encode())
            except OSError:
                self._drop()
                return False
            try:
                self.sock.settimeout(0.0)
                try:
                    self.sock.recv(4096)
                except (BlockingIOError, OSError):
                    pass  # no reply waiting: normal for `set`
                finally:
                    self.sock.settimeout(0.6)
            except OSError:
                self._drop()
                return False
            return True

    def ask(self, line: str) -> str:
        """Send command and read reply up to the next prompt."""
        with self.lock:
            if not self._ensure():
                return ""
            try:
                self.sock.sendall((line + "\n").encode())
            except OSError:
                self._drop()
                return ""
            chunks = []
            try:
                while True:
                    data = self.sock.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
                    if b"> " in data:
                        break
            except (socket.timeout, OSError):
                pass
            return b"".join(chunks).decode(errors="replace")

    # -- effect helpers (fluidsynth 2.x: live-settable synth.* settings) --
    def reverb_level(self, v01: float):
        self.send(f"set synth.reverb.level {v01:.3f}")

    def reverb_room(self, v01: float):
        self.send(f"set synth.reverb.room-size {v01:.3f}")

    def reverb_damp(self, v01: float):
        self.send(f"set synth.reverb.damp {v01:.3f}")

    def chorus_level(self, v: float):
        self.send(f"set synth.chorus.level {v:.3f}")


def parse_float_reply(text: str):
    """Extract first float from a shell reply ('roomsize: 0.200' -> 0.2)."""
    import re
    m = re.search(r"[-+]?\d*\.\d+|[-+]?\d+", text.replace(",", "."))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def spawn(cmd):
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
