#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSTV Station for the Icom IC-7300  -  SSTV receive and transmit, with panadapter
SSTV Station voor de Icom IC-7300  -  SSTV ontvangen en zenden, met panadapter

Copyright (c) 2026 Richard A. Hendriksen (PD3RAH)
Licensed under the MIT License - see LICENSE.

Not affiliated with or endorsed by Icom Inc. "Icom" and "IC-7300" are
trademarks of Icom Inc. and are used only to describe compatibility.

Requires / Vereist:  pip install pyserial sounddevice numpy pillow
Run / Start:         python sstv_station.py
"""

import json
import math
import os
import sys
import queue
import threading
import time
import tkinter as tk
import re
import webbrowser
from tkinter import ttk, messagebox, filedialog
import tkinter.font as tkfont

import numpy as np

import sstv

try:
    import serial
except ImportError:
    serial = None
try:
    import sounddevice as sd
except (ImportError, OSError):
    sd = None
try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:
    Image = None

# ----------------------------------------------------------------- settings / instellingen
APP_NAME = "SSTV Station"
APP_VERSION = "1.0.0"
SURVEY_URL = "https://www.pd3rah.nl/sstvstation/enquete.php"
REPO_URL = "https://github.com/PD3RAH/sstv-station"
RELEASE_API = "https://api.github.com/repos/PD3RAH/sstv-station/releases/latest"
RADIOS = {0x94: "IC-7300", 0xB6: "IC-7300MK2"}     # standaard CI-V-adressen
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "SSTVStation")
CONFIG_FILE = os.path.join(DATA_DIR, "settings.json")
LOG_FILE = os.path.join(DATA_DIR, "logbook.adi")
RX_DIR = os.path.join(DATA_DIR, "received")
DEFAULT_CONFIG = {
    "port": "COM4",
    "baud": 115200,
    "civ_addr": "auto",
    "mycall": "",
    "myname": "",
    "myqth": "",
    "audio_device": "",
    "audio_out": "",
    "lang": "nl",
    "theme": "Amber",
    "check_updates": True,
    "audio_range": 3000,
    "tx_mode": "M1",
    "rx_mode": "auto",
    "tx_level": 50,
    "tx_image": "",
    "inset": False,
    "slant": True,
    "freerun": True,
    "preamble": True,
    "autostart": True,
    "rx_filter": "normal",
    "denoise": True,
    "templates": {
        "F1": ["CQ", "CQ CQ SSTV|DE {MYCALL}|{MYQTH}"],
        "F2": ["Reply", "{CALL} DE {MYCALL}|UR RSV {RSV}|{MYNAME}  {MYQTH}"],
        "F3": ["Report", "{CALL}|TNX PIC  UR {RSV}|DE {MYCALL}"],
        "F4": ["73", "TNX QSO {CALL}|73 DE {MYCALL}"],
        "F5": ["QRZ?", "QRZ?|DE {MYCALL}"],
        "F6": ["My call", "{MYCALL}"],
        "F7": ["Test", "TEST|DE {MYCALL}"],
        "F8": ["No text", ""],
    },
}

CTRL_ADDR = 0xE0
MODES = {0x00: "LSB", 0x01: "USB", 0x02: "AM", 0x03: "CW", 0x04: "RTTY",
         0x05: "FM", 0x07: "CW-R", 0x08: "RTTY-R"}
# SSTV-frequenties (gebruikelijke aanroepfrequenties); LSB onder 10 MHz, daarboven USB
BANDS = [("80", 3735000), ("40", 7165000), ("20", 14230000), ("17", 18160000),
         ("15", 21340000), ("12", 24975000), ("10", 28680000), ("6", 50510000)]
BAND_RANGES = {"160": (1.8e6, 2.0e6), "80": (3.5e6, 3.8e6), "40": (7.0e6, 7.2e6),
               "30": (10.1e6, 10.15e6), "20": (14.0e6, 14.35e6), "17": (18.068e6, 18.168e6),
               "15": (21.0e6, 21.45e6), "12": (24.89e6, 24.99e6), "10": (28.0e6, 29.7e6),
               "6": (50.0e6, 52.0e6), "4": (70.0e6, 70.5e6)}
SSTV_FREQS = [("80 m", 3735000, "LSB", "EU 3.730-3.740"), ("40 m", 7165000, "LSB", "EU 7.165 / 7.171"),
              ("20 m", 14230000, "USB", "14.230 / 14.233"), ("17 m", 18160000, "USB", "18.160"),
              ("15 m", 21340000, "USB", "21.340"), ("12 m", 24975000, "USB", "24.975"),
              ("10 m", 28680000, "USB", "28.680"), ("6 m", 50510000, "USB", "50.510")]
# ----------------------------------------------------------------- BCD helpers
def bcd_byte(b):
    return (b >> 4) * 10 + (b & 0x0F)


def bcd_le_to_int(data):
    val, mult = 0, 1
    for b in data:
        val += bcd_byte(b) * mult
        mult *= 100
    return val


def int_to_bcd_le(v, n):
    out = []
    for _ in range(n):
        two = v % 100
        v //= 100
        out.append(((two // 10) << 4) | (two % 10))
    return bytes(out)


def int_to_bcd_be(v, n):
    return int_to_bcd_le(v, n)[::-1]


# ----------------------------------------------------------------- CI-V
class CIV:
    """Seriële CI-V verbinding met frame-parser in een eigen thread."""

    def __init__(self, port, baud, addr, on_frame):
        # DTR/RTS laag houden: de IC-7300 kan deze lijnen gebruiken voor
        # USB SEND (PTT) en USB Keying (CW). pyserial zet ze standaard hoog,
        # waardoor de radio meteen gaat zenden.
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = baud
        self.ser.timeout = 0.05
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.open()
        self.ser.dtr = False
        self.ser.rts = False
        self.addr = addr
        self.on_frame = on_frame
        self.lock = threading.Lock()
        self.running = True
        self.buf = bytearray()
        threading.Thread(target=self._reader, daemon=True).start()

    def send(self, cmd, data=b""):
        self.send_to(self.addr, cmd, data)

    def send_to(self, addr, cmd, data=b""):
        frame = bytes([0xFE, 0xFE, addr, CTRL_ADDR, cmd]) + bytes(data) + b"\xFD"
        with self.lock:
            self.ser.write(frame)

    def close(self):
        self.running = False
        time.sleep(0.1)
        try:
            self.ser.close()
        except Exception:
            pass

    def _reader(self):
        while self.running:
            try:
                chunk = self.ser.read(2048)
            except Exception:
                break
            if not chunk:
                continue
            self.buf += chunk
            while True:
                start = self.buf.find(b"\xFE\xFE")
                if start < 0:
                    keep = self.buf[-1:] if self.buf[-1:] == b"\xFE" else b""
                    self.buf = bytearray(keep)
                    break
                end = self.buf.find(b"\xFD", start)
                if end < 0:
                    if start > 0:
                        del self.buf[:start]
                    break
                payload = bytes(self.buf[start + 2:end])
                del self.buf[:end + 1]
                while payload[:1] == b"\xFE":
                    payload = payload[1:]
                if len(payload) < 3:
                    continue
                to, frm, cmd, data = payload[0], payload[1], payload[2], payload[3:]
                if frm == CTRL_ADDR:            # echo van eigen commando
                    continue
                if to not in (CTRL_ADDR, 0x00):
                    continue
                try:
                    self.on_frame(cmd, data, frm)
                except Exception as e:
                    print("frame-fout:", e)




def log_error(where, exc):
    try:
        import traceback
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "error.log"), "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {where} (v{APP_VERSION})\n")
            f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:] + "\n")
    except Exception:
        pass


def _winhttp_get(url, timeout=8, headers=""):
    """HTTP(S) GET via Windows WinHTTP (gebruikt de TLS en proxy-instellingen van Windows)."""
    import ctypes
    from ctypes import wintypes
    from urllib.parse import urlsplit
    w = ctypes.WinDLL("winhttp")
    H = ctypes.c_void_p
    w.WinHttpOpen.restype = H
    w.WinHttpOpen.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    w.WinHttpConnect.restype = H
    w.WinHttpConnect.argtypes = [H, wintypes.LPCWSTR, wintypes.WORD, wintypes.DWORD]
    w.WinHttpOpenRequest.restype = H
    w.WinHttpOpenRequest.argtypes = [H, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                     ctypes.c_void_p, wintypes.DWORD]
    w.WinHttpSendRequest.argtypes = [H, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                     wintypes.DWORD, ctypes.c_size_t]
    w.WinHttpReceiveResponse.argtypes = [H, ctypes.c_void_p]
    w.WinHttpQueryDataAvailable.argtypes = [H, ctypes.POINTER(wintypes.DWORD)]
    w.WinHttpReadData.argtypes = [H, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    w.WinHttpQueryHeaders.argtypes = [H, wintypes.DWORD, wintypes.LPCWSTR, ctypes.c_void_p,
                                      ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    w.WinHttpSetTimeouts.argtypes = [H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    w.WinHttpCloseHandle.argtypes = [H]
    u = urlsplit(url)
    secure = u.scheme == "https"
    port = u.port or (443 if secure else 80)
    path = (u.path or "/") + (("?" + u.query) if u.query else "")
    agent = f"SSTVStation/{APP_VERSION}"
    hs = w.WinHttpOpen(agent, 4, None, None, 0) or w.WinHttpOpen(agent, 0, None, None, 0)
    hc = hr = None
    try:
        if not hs:
            raise OSError("WinHttpOpen failed")
        ms = int(timeout * 1000)
        w.WinHttpSetTimeouts(hs, ms, ms, ms, ms)
        hc = w.WinHttpConnect(hs, u.hostname, port, 0)
        hr = w.WinHttpOpenRequest(hc, "GET", path, None, None, None, 0x00800000 if secure else 0)
        if not hr:
            raise OSError("WinHttpOpenRequest failed")
        if not w.WinHttpSendRequest(hr, headers or None, 0xFFFFFFFF if headers else 0, None, 0, 0, 0):
            raise OSError(f"WinHttpSendRequest failed ({ctypes.GetLastError()})")
        if not w.WinHttpReceiveResponse(hr, None):
            raise OSError(f"WinHttpReceiveResponse failed ({ctypes.GetLastError()})")
        code, size = wintypes.DWORD(0), wintypes.DWORD(4)
        w.WinHttpQueryHeaders(hr, 19 | 0x20000000, None, ctypes.byref(code), ctypes.byref(size), None)
        data = b""
        while True:
            n = wintypes.DWORD(0)
            if not w.WinHttpQueryDataAvailable(hr, ctypes.byref(n)) or n.value == 0:
                break
            buf = ctypes.create_string_buffer(n.value)
            got = wintypes.DWORD(0)
            w.WinHttpReadData(hr, buf, n.value, ctypes.byref(got))
            data += buf.raw[:got.value]
        if code.value and code.value >= 400:
            raise OSError(f"HTTP {code.value}")
        return data
    finally:
        for hnd in (hr, hc, hs):
            if hnd:
                w.WinHttpCloseHandle(hnd)


def http_get(url, timeout=8, accept="application/vnd.github+json"):
    hdr = f"Accept: {accept}\r\n"
    try:
        import ssl  # noqa: F401  (niet aanwezig in de gebundelde runtime)
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": f"SSTVStation/{APP_VERSION}",
                                                   "Accept": accept})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except ImportError:
        return _winhttp_get(url, timeout, hdr)


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)



# ----------------------------------------------------------------- audio-ingang (RX)
class AudioEngine:
    FFT = 4096

    def __init__(self, device, on_wf_row, decoder_factory):
        info = sd.query_devices(device, "input")
        fs = 48000
        try:
            sd.check_input_settings(device=device, samplerate=fs, channels=1)
        except Exception:
            fs = int(info["default_samplerate"])
        self.fs = fs
        self.decoder = decoder_factory(fs)
        self.on_wf_row = on_wf_row
        self.q = queue.Queue(maxsize=400)
        self.hop = fs // 20
        self.buf = np.zeros(0, dtype=np.float32)
        self.win = np.hanning(self.FFT).astype(np.float32)
        self.running = True
        self.level_db = -200.0
        self.blocks = 0
        self.dropped = 0
        self.lost = 0
        self.rec = None
        self.rec_n = 0
        self.rec_full = False
        self.error = None
        self.stream = sd.InputStream(device=device, channels=1, samplerate=fs,
                                     blocksize=fs // 100, dtype="float32",
                                     callback=self._cb)
        threading.Thread(target=self._worker, daemon=True).start()
        self.stream.start()

    def _cb(self, indata, frames, t, status):
        try:
            self.q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            self.dropped += frames                      # later als stilte invoegen: tijdlijn blijft kloppen

    def _worker(self):
        since = 0
        while self.running:
            try:
                blks = [self.q.get(timeout=0.2)]
            except queue.Empty:
                continue
            while len(blks) < 50:                       # achterstand in één keer verwerken
                try:
                    blks.append(self.q.get_nowait())
                except queue.Empty:
                    break
            try:
                if self.dropped:
                    n, self.dropped = self.dropped, 0
                    self.lost += n
                    self.decoder.process(np.zeros(n, dtype=np.float32))
                blk = np.concatenate(blks)
                if self.rec is not None:
                    self.rec.append(blk)
                    self.rec_n += len(blk)
                    if self.rec_n > self.fs * 600:          # max 10 minuten
                        self.rec_full = True
                rms = float(np.sqrt(np.mean(blks[-1].astype(np.float64) ** 2)))
                self.level_db = 20 * math.log10(rms) if rms > 1e-9 else -200.0
                self.blocks += len(blks)
                self.decoder.process(blk)
                self.buf = np.concatenate([self.buf, blk])[-self.FFT:]
                since += len(blk)
                if since >= self.hop and len(self.buf) == self.FFT:
                    since = 0
                    spec = np.abs(np.fft.rfft(self.buf * self.win))
                    self.on_wf_row(spec, self.fs)
            except Exception as e:                      # nooit stilletjes stoppen
                self.error = f"{type(e).__name__}: {e}"
                log_error("audio", e)

    def close(self):
        self.running = False
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass


# ----------------------------------------------------------------- SSTV-zender (TX)
class SSTVSender:
    """Genereert het SSTV-signaal, zet de radio via CI-V op zenden en speelt het af op de USB-audio."""

    def __init__(self, app):
        self.app = app
        self.abort = threading.Event()
        self.busy = False
        self.progress = 0.0
        self.total = 0.0
        self.t_start = 0.0

    def start(self, mode, img, device, level, preamble):
        if self.busy:
            return False
        self.abort.clear()
        self.busy = True
        self.progress = 0.0
        threading.Thread(target=self._run, args=(mode, img, device, level, preamble), daemon=True).start()
        return True

    def _run(self, mode, img, device, level, preamble):
        ptt = False
        try:
            fs = 48000
            try:
                sd.check_output_settings(device=device, samplerate=fs, channels=1)
            except Exception:
                fs = int(sd.query_devices(device, "output")["default_samplerate"])
            info = sd.query_devices(device, "output")
            nch = 2 if info["max_output_channels"] >= 2 else 1
            audio = sstv.encode(mode, img, fs, amp=level, preamble=preamble)
            self.total = len(audio) / fs
            civ = self.app.civ
            if civ is None or self.abort.is_set():
                return
            civ.send(0x1C, b"\x00\x01")                 # PTT aan
            ptt = True
            self.t_start = time.time()
            self.app.gq.put(("tx", mode.name, self.total))
            time.sleep(0.25)                             # zender laten opkomen
            pos = [0]
            done = threading.Event()

            def cb(out, frames, t, status):
                i = pos[0]
                chunk = audio[i:i + frames]
                n = len(chunk)
                out[:n, :] = chunk[:, None]
                out[n:, :] = 0
                pos[0] = i + n
                if n < frames or self.abort.is_set():
                    raise sd.CallbackStop

            with sd.OutputStream(device=device, samplerate=fs, channels=nch, dtype="float32",
                                 blocksize=1024, callback=cb, finished_callback=done.set):
                while not done.wait(0.1):
                    self.progress = pos[0] / len(audio)
                    if self.abort.is_set():
                        break
                    if time.time() - self.t_start > self.total + 15:      # waakhond
                        break
            self.progress = 1.0
            time.sleep(0.15)
        except Exception as e:
            log_error("tx", e)
            self.app.gq.put(("txerr", f"{type(e).__name__}: {e}"))
        finally:
            civ = self.app.civ
            if civ and ptt:
                civ.send(0x1C, b"\x00\x00")             # PTT uit
            self.busy = False
            self.app.gq.put(("txdone", None))

    def stop(self):
        self.abort.set()
        civ = self.app.civ
        if civ:
            civ.send(0x1C, b"\x00\x00")


# ----------------------------------------------------------------- beelden
def write_wav(path, x, fs):
    import wave
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(fs))
        w.writeframes(pcm.tobytes())


def read_wav(path):
    """WAV (8/16/24/32-bit PCM, mono of stereo) -> (float32 mono, samplerate)."""
    import wave
    with wave.open(path, "rb") as w:
        ch, sw, fs, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    elif sw == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
    elif sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = (np.where(v & 0x800000, v - 0x1000000, v)).astype(np.float32) / 8388608
    elif sw == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648
    else:
        raise ValueError(f"unsupported sample width {sw}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x.astype(np.float32), fs


def signed(v, unit):
    v = int(round(v))
    return f"{v:+d} {unit}" if v else f"0 {unit}"


def photo_from_array(arr):
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    h, w = arr.shape[:2]
    return tk.PhotoImage(data=b"P6 %d %d 255 " % (w, h) + arr.tobytes(), format="PPM")


def fit_size(w, h, cw, ch):
    f = min(cw / w, ch / h)
    return max(1, int(w * f)), max(1, int(h * f))


_FONTS = {}


def load_font(size):
    if size in _FONTS:
        return _FONTS[size]
    f = None
    for name in ("arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            f = ImageFont.truetype(name, size)
            break
        except OSError:
            pass
    if f is None:
        try:
            f = ImageFont.load_default(size=size)
        except TypeError:
            f = ImageFont.load_default()
    _FONTS[size] = f
    return f


def default_background(w, h):
    """Rustige achtergrond als er geen eigen beeld gekozen is."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.hypot(x / w - 0.3, y / h - 0.35)
    img = np.zeros((h, w, 3), np.float32)
    img[..., 0] = 20 + 60 * (x / w)
    img[..., 1] = 60 + 90 * np.exp(-3 * r)
    img[..., 2] = 110 + 120 * np.exp(-2 * r)
    img[(np.abs((x % 40) - 20) < 0.6) | (np.abs((y % 40) - 20) < 0.6)] *= 1.25
    return np.clip(img, 0, 255).astype(np.uint8)


def compose_tx(mode, src, lines, inset=None):
    """Zendbeeld maken: achtergrond (bijgesneden op modusformaat), optioneel inzet, tekst met omlijning."""
    W, H = mode.width, mode.height
    if src is not None:
        base = ImageOps.fit(src.convert("RGB"), (W, H), Image.LANCZOS)
    else:
        base = Image.fromarray(default_background(W, H))
    if inset is not None:
        iw, ih = int(W * 0.38), int(H * 0.38)
        im = Image.fromarray(np.asarray(inset)).convert("RGB").resize((iw, ih), Image.LANCZOS)
        x, y = W - iw - max(3, W // 60), int(H * 0.30)
        ImageDraw.Draw(base).rectangle([x - 2, y - 2, x + iw + 1, y + ih + 1], fill=(255, 255, 255))
        base.paste(im, (x, y))
    d = ImageDraw.Draw(base)
    lines = [ln.strip() for ln in lines]

    def put(text, size, y, fill, anchor):
        if not text:
            return
        while size > 8:
            f = load_font(size)
            if d.textlength(text, font=f) <= W * 0.94:
                break
            size -= 1
        f = load_font(size)
        d.text((W / 2, y), text, font=f, fill=fill, anchor=anchor,
               stroke_width=max(2, size // 11), stroke_fill=(0, 0, 0))
    if lines:
        put(lines[0], H // 7, H * 0.03, (255, 235, 40), "ma")
    rest = [ln for ln in lines[1:] if ln]
    sz = H // 11
    y = H - H * 0.03
    for ln in reversed(rest):
        put(ln, sz, y, (255, 255, 255), "md")
        y -= sz * 1.18
    return np.asarray(base, dtype=np.uint8).copy()


def load_image(path):
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    return im.convert("RGB")


# ----------------------------------------------------------------- waterval-widget
def make_lut():
    pts = [(0.0, (0, 0, 0)), (0.2, (0, 0, 120)), (0.4, (0, 120, 220)),
           (0.6, (0, 220, 120)), (0.75, (240, 230, 0)), (0.9, (255, 60, 0)),
           (1.0, (255, 255, 255))]
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        x = i / 255
        for (x0, c0), (x1, c1) in zip(pts, pts[1:]):
            if x0 <= x <= x1:
                f = (x - x0) / (x1 - x0)
                lut[i] = [int(c0[k] + f * (c1[k] - c0[k])) for k in range(3)]
                break
    return lut


LUT = make_lut()


class Waterfall:
    def __init__(self, parent, hs, hw, on_click=None, label_fn=None):
        self.hs, self.hw = hs, hw
        self.width = 900
        self.canvas = tk.Canvas(parent, bg="#030a10", height=hs + hw, width=self.width,
                                highlightthickness=0, cursor="crosshair")
        self.arr = np.zeros((hw, self.width, 3), dtype=np.uint8)
        self.row = None
        self.dirty = False
        self.photo = None
        self.img_id = None
        self.markers = []       # (frac, kleur, breedte_frac)
        self.labels = []        # (frac, tekst, kleur)
        self.boxes = []         # (frac_laag, frac_hoog, tekst, kleur)  bv. herkende SSTV-signalen
        self.label_fn = label_fn
        self.on_click = on_click
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<Button-1>", self._click)

    def _resize(self, ev):
        w = max(100, ev.width)
        if w != self.width:
            self.width = w
            self.arr = np.zeros((self.hw, w, 3), dtype=np.uint8)
            if self.row is not None:            # spectrumlijn meeschalen naar de nieuwe breedte
                self.row = np.interp(np.linspace(0, len(self.row) - 1, w), np.arange(len(self.row)), self.row)
            self.dirty = True

    def _click(self, ev):
        if self.on_click:
            self.on_click(min(max(ev.x / self.width, 0.0), 1.0))

    def add_row(self, vals):
        vals = np.asarray(vals, dtype=np.float32)
        if len(vals) < 2:
            return
        row = np.interp(np.linspace(0, len(vals) - 1, self.width),
                        np.arange(len(vals)), vals)
        row = np.clip(row, 0, 1)
        self.arr[1:] = self.arr[:-1]
        self.arr[0] = LUT[(row * 255).astype(np.uint8)]
        self.row = row
        self.dirty = True

    def render(self):
        if not self.dirty:
            return
        self.dirty = False
        c = self.canvas
        h, w = self.arr.shape[:2]
        self.photo = tk.PhotoImage(data=b"P6 %d %d 255 " % (w, h) + self.arr.tobytes(), format="PPM")
        if self.img_id is None:
            self.img_id = c.create_image(0, self.hs, anchor="nw", image=self.photo)
        else:
            c.itemconfig(self.img_id, image=self.photo)
        c.delete("ov")
        # schaal
        if self.label_fn:
            for i in range(1, 10):
                x = self.width * i / 10
                c.create_line(x, 0, x, self.hs, fill=_mix(C["scr"], C["border"], 0.6), tags="ov")
                c.create_text(x + 3, 3, anchor="nw", text=self.label_fn(i / 10),
                              fill=C["dim"], font=("Consolas", S(8)), tags="ov")
        # spectrum: gevulde vorm + lijn
        if self.row is not None:
            n = min(len(self.row), self.width)
            step = max(1, n // 600)
            xs = np.arange(0, n, step)
            ys = self.hs - 2 - self.row[xs] * (self.hs - 16)
            pts = np.column_stack([xs, ys]).ravel().tolist()
            if len(pts) >= 4:
                c.create_polygon([0, self.hs] + pts + [xs[-1], self.hs],
                                 fill=_mix(C["scr"], C["acc"], 0.18), outline="", tags="ov")
                c.create_line(*pts, fill=C["acc"], width=1, tags="ov")
        for frac, color, wfrac in self.markers:
            x = frac * self.width
            if wfrac:
                wx = wfrac * self.width / 2
                c.create_rectangle(x - wx, 0, x + wx, self.hs, outline="",
                                   fill=color, stipple="gray25", tags="ov")
            c.create_line(x, 0, x, self.hs + self.hw, fill=color, tags="ov")
        for f0, f1, text, color in self.boxes:
            x0, x1 = f0 * self.width, f1 * self.width
            c.create_rectangle(x0, 14, x1, self.hs + self.hw - 1, outline=color, width=2, dash=(5, 3), tags="ov")
            tid = c.create_text((x0 + x1) / 2, 16, anchor="n", text=text, fill="#03141c",
                                font=("Segoe UI", S(8), "bold"), tags="ov")
            bb = c.bbox(tid)
            if bb:
                r = c.create_rectangle(bb[0] - 4, bb[1] - 1, bb[2] + 4, bb[3] + 1, fill=color, outline="", tags="ov")
                c.tag_lower(r, tid)
        for frac, text, color in self.labels:
            x = frac * self.width
            tid = c.create_text(x + 4, self.hs + 4, anchor="nw", text=text, fill="#ffffff",
                                font=("Segoe UI", S(8), "bold"), tags="ov")
            bb = c.bbox(tid)
            if bb:
                r = c.create_rectangle(bb[0] - 3, bb[1] - 1, bb[2] + 3, bb[3] + 1, fill=color,
                                       outline="", tags="ov")
                c.tag_lower(r, tid)
                c.create_line(x, self.hs, x, self.hs + 4, fill=color, tags="ov")


# ----------------------------------------------------------------- thema & widgets
C = dict(bg="#07121b", panel="#0c1d2b", panel2="#10283b", border="#1a3d55", btn="#132e45",
         btnhi="#1b4160", acc="#ffb02e", acc2="#5cc8ff", text="#d8edf6", dim="#6f93a8",
         red="#ff4d5e", amber="#ff9f43", green="#4be38a", scr="#030a10")
THEMES = {
    "Amber":     dict(acc="#ffb02e", acc2="#5cc8ff"),
    "Turquoise": dict(acc="#2ee6d6", acc2="#ffb43c"),
    "Green":     dict(acc="#63e68a", acc2="#ffb43c"),
    "Ice blue":  dict(acc="#5cb8ff", acc2="#ffb43c"),
    "Copper":    dict(acc="#ff8a4c", acc2="#7fd6ff"),
    "Purple":    dict(acc="#b78cff", acc2="#ffc35c"),
}
THEME_NL = {"Amber": "Amber", "Turquoise": "Turquoise", "Green": "Groen", "Ice blue": "IJsblauw",
            "Copper": "Koper", "Purple": "Paars"}
UI = "Segoe UI"
MONO = "Consolas"
DIGIT = "Bahnschrift"
SCALE = 1.0          # wordt aangepast aan de venstergrootte (responsive)


def S(v):
    """Pixel- of puntmaat schalen met de huidige UI-schaal."""
    return max(1, int(round(v * SCALE)))


def scale_for(w, h):
    """Schaal bepalen uit de beschikbare venstergrootte (ontwerpgrootte 1560 x 1000)."""
    return round(min(max(min(w / 1560.0, h / 1000.0), 0.66), 1.7), 2)


def _mix(c1, c2, f):
    f = min(max(f, 0.0), 1.0)
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(x + (y - x) * f):02x}" for x, y in zip(a, b))


def trace(widget, var, cb):
    """Variabele volgen; trace wordt opgeruimd als de widget verdwijnt."""
    tid = var.trace_add("write", lambda *a: cb())

    def _rm(ev):
        if ev.widget is widget:
            try:
                var.trace_remove("write", tid)
            except tk.TclError:
                pass
    widget.bind("<Destroy>", _rm, add="+")


def panel(parent, title=None):
    """Kaart met dunne rand; geeft (frame, body, kop) terug."""
    f = tk.Frame(parent, bg=C["panel"], highlightthickness=1, highlightbackground=C["border"])
    head = None
    if title is not None:
        head = tk.Frame(f, bg=C["panel"])
        head.pack(fill="x", padx=12, pady=(8, 0))
        tk.Frame(head, bg=C["acc"], width=3, height=12).pack(side="left", padx=(0, 7))
        lbl = tk.Label(head, text=title, bg=C["panel"], fg=C["dim"], font=(UI, S(9), "bold"))
        lbl.pack(side="left")
        head.title = lbl
    body = tk.Frame(f, bg=C["panel"])
    body.pack(fill="both", expand=True, padx=12, pady=6)
    return f, body, head


def label(parent, text="", fg=None, font=None, bg=None, **kw):
    return tk.Label(parent, text=text, bg=bg or parent.cget("bg"), fg=fg or C["dim"],
                    font=font or (UI, S(9)), **kw)


def entry(parent, var, width=8, font=None):
    return tk.Entry(parent, textvariable=var, width=width, bg=C["panel2"], fg=C["text"],
                    insertbackground=C["acc"], relief="flat", font=font or (UI, S(10)),
                    highlightthickness=1, highlightbackground=C["border"], highlightcolor=C["acc"])


class FlatButton(tk.Canvas):
    """Afgeronde knop (canvas). Optioneel gekoppeld aan een BooleanVar (toggle)."""

    def __init__(self, parent, text, command=None, var=None, width=None, font=None,
                 accent=None, padx=12, pady=5, radius=8):
        self.font = tkfont.Font(font=font or (UI, S(10), "bold"))
        self._text = text
        self.padx, self.pady, self.radius = padx, pady, radius
        self.minw = (width or 0) * self.font.measure("0")
        w, h = self._size()
        super().__init__(parent, width=w, height=h, bg=parent.cget("bg"),
                         highlightthickness=0, cursor="hand2")
        self.command = command
        self.var = var
        self.accent = accent or C["acc"]
        self.active = False
        self.hover = False
        self.fg_override = None
        self.bind("<Enter>", lambda e: self._h(True))
        self.bind("<Leave>", lambda e: self._h(False))
        self.bind("<Button-1>", self._click)
        self.bind("<Configure>", lambda e: self._paint())
        if var is not None:
            trace(self, var, lambda: self.set_active(bool(var.get())))
            self.active = bool(var.get())
        self._paint()

    def _size(self):
        return (max(self.font.measure(self._text) + 2 * self.padx, self.minw),
                self.font.metrics("linespace") + 2 * self.pady)

    def config(self, cnf=None, **kw):
        if "text" in kw:
            self._text = kw.pop("text")
            w, h = self._size()
            kw.setdefault("width", w)
        if "fg" in kw:
            self.fg_override = kw.pop("fg")
        if kw:
            super().config(cnf, **kw)
        self._paint()
    configure = config

    def _h(self, on):
        self.hover = on
        self._paint()

    def _click(self, ev):
        if self.var is not None:
            self.var.set(not self.var.get())
        if self.command:
            self.command()

    def set_active(self, on, accent=None):
        self.active = bool(on)
        if accent:
            self.accent = accent
        self._paint()

    def _paint(self):
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        if w <= 1:
            w, h = self._size()
        self.delete("all")
        r = min(self.radius, h / 2)
        if self.active:
            bg, fg, ol = self.accent, "#03131b", self.accent
        else:
            bg = C["btnhi"] if self.hover else C["btn"]
            fg = self.fg_override or C["text"]
            ol = _mix(C["border"], C["acc"], 0.35) if self.hover else C["border"]
        x1, y1, x2, y2 = 1, 1, w - 1, h - 1
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        self.create_polygon(pts, smooth=True, fill=bg, outline=ol)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self.font)


class Slider(tk.Canvas):
    def __init__(self, parent, var, lo, hi, command=None, width=200, height=22):
        super().__init__(parent, width=width, height=height, bg=parent.cget("bg"),
                         highlightthickness=0, cursor="hand2")
        self.var, self.lo, self.hi, self.command = var, lo, hi, command
        self.w, self.h = width, height
        self.bind("<Button-1>", self._set)
        self.bind("<B1-Motion>", self._set)
        self.bind("<ButtonRelease-1>", lambda e: self.command and self.command(True))
        self.bind("<MouseWheel>", lambda e: self._step(1 if e.delta > 0 else -1))
        self.bind("<Configure>", lambda e: self._resize(e.width))
        trace(self, var, self.draw)
        self.draw()

    def _resize(self, w):
        self.w = w
        self.draw()

    def _set(self, ev):
        f = min(max((ev.x - 8) / max(self.w - 16, 1), 0), 1)
        self.var.set(round(self.lo + f * (self.hi - self.lo), 2))
        if self.command:
            self.command(False)

    def _step(self, d):
        v = self.var.get() + d * (self.hi - self.lo) / 50
        self.var.set(round(min(max(v, self.lo), self.hi), 2))
        if self.command:
            self.command(True)

    def draw(self):
        self.delete("all")
        try:
            v = float(self.var.get())
        except (ValueError, tk.TclError):
            return
        f = (v - self.lo) / (self.hi - self.lo)
        y = self.h // 2
        x = 8 + f * (self.w - 16)
        k = max(5, int(self.h * 0.33))
        lw = max(3, int(self.h * 0.18))
        self.create_line(8, y, self.w - 8, y, fill=C["btn"], width=lw, capstyle="round")
        self.create_line(8, y, x, y, fill=C["acc"], width=lw, capstyle="round")
        self.create_oval(x - k, y - k, x + k, y + k, fill=C["text"], outline=C["acc"], width=2)


class Knob(tk.Canvas):
    """VFO-knop met rubberen kartelrand, vingerkuil en geborstelde kap."""

    RIDGES = 64

    def __init__(self, parent, size, on_step):
        super().__init__(parent, width=size, height=size, bg=C["panel"],
                         highlightthickness=0, cursor="hand2")
        self.size, self.on_step = size, on_step
        self.angle = 90.0
        self.acc_deg = 0.0
        self.last = None
        c = size / 2
        R = c - 3
        self.c = c
        for i in range(40):
            a = math.radians(i * 9)
            big = i % 5 == 0
            d = 2.2 if big else 1.2
            x, y = c + (R - 1) * math.cos(a), c - (R - 1) * math.sin(a)
            self.create_oval(x - d, y - d, x + d, y + d, outline="",
                             fill=C["acc"] if big else C["dim"])
        self.Rk = R - 10
        self.Rs = self.Rk - 11
        self.Rc = self.Rs - 7
        self.create_oval(c - self.Rk + 4, c - self.Rk + 6, c + self.Rk + 4, c + self.Rk + 6,
                         fill="#02070b", outline="")
        self.create_oval(c - self.Rk - 2, c - self.Rk - 2, c + self.Rk + 2, c + self.Rk + 2,
                         outline=_mix(C["acc"], C["panel"], 0.45), width=2)
        self.create_oval(c - self.Rk, c - self.Rk, c + self.Rk, c + self.Rk, fill="#16191c", outline="#000")
        for k in range(8):
            r = self.Rs - k
            self.create_oval(c - r, c - r, c + r, c + r, outline="", fill=_mix("#1e2328", "#2b3238", k / 8))
        for k in range(26):
            r = self.Rc - k * self.Rc / 26
            g = 0.25 + 0.5 * (0.5 + 0.5 * math.sin(k * 1.7)) * 0.25 + 0.35 * (k / 26)
            self.create_oval(c - r, c - r, c + r, c + r, outline="", fill=_mix("#3a434b", "#8d98a2", g))
        self.create_arc(c - self.Rc + 6, c - self.Rc + 6, c + self.Rc - 6, c + self.Rc - 6,
                        start=100, extent=70, style="arc", outline="#c9d2d9", width=2)
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<MouseWheel>", lambda e: self._turn(1 if e.delta > 0 else -1))
        self.bind("<Button-4>", lambda e: self._turn(1))
        self.bind("<Button-5>", lambda e: self._turn(-1))
        self._draw_rot()

    def _draw_rot(self):
        self.delete("rot")
        c, Rk, Rs, Rc = self.c, self.Rk, self.Rs, self.Rc
        n = self.RIDGES
        for i in range(n):
            a0 = math.radians(self.angle + i * 360 / n)
            a1 = a0 + math.radians(360 / n * 0.55)
            light = 0.5 + 0.5 * math.cos(a0 - math.radians(135))
            col = _mix("#1b1f23", "#59626b", light * 0.9)
            pts = []
            for a, r in ((a0, Rk - 1), (a1, Rk - 1), (a1, Rs + 1), (a0, Rs + 1)):
                pts += [c + r * math.cos(a), c - r * math.sin(a)]
            self.create_polygon(pts, fill=col, outline="", tags="rot")
        a = math.radians(self.angle + 200)
        rd = Rc * 0.30
        dx, dy = c + Rc * 0.50 * math.cos(a), c - Rc * 0.50 * math.sin(a)
        self.create_oval(dx - rd, dy - rd, dx + rd, dy + rd, fill="#2a3138", outline="#1a1f24", width=2, tags="rot")
        self.create_oval(dx - rd * .75, dy - rd * .75 + 2, dx + rd * .75, dy + rd * .75 + 2, fill="#4a545d",
                         outline="", tags="rot")
        self.create_oval(dx - rd * .7, dy - rd * .7, dx + rd * .7, dy + rd * .7, fill="#373f46", outline="", tags="rot")
        a = math.radians(self.angle)
        r1, r2 = Rc - 4, Rc - 16
        self.create_line(c + r1 * math.cos(a), c - r1 * math.sin(a), c + r2 * math.cos(a), c - r2 * math.sin(a),
                         fill=C["acc"], width=4, capstyle="round", tags="rot")

    def _ang(self, ev):
        return math.degrees(math.atan2(self.c - ev.y, ev.x - self.c))

    def _press(self, ev):
        self.last = self._ang(ev)

    def _drag(self, ev):
        a = self._ang(ev)
        d = a - self.last
        if d > 180:
            d -= 360
        if d < -180:
            d += 360
        self.last = a
        self.acc_deg += -d
        while abs(self.acc_deg) >= 10:
            s = 1 if self.acc_deg > 0 else -1
            self.acc_deg -= 10 * s
            self._turn(s)

    def _turn(self, s):
        self.angle -= 10 * s
        self._draw_rot()
        self.on_step(s)


class BarMeter(tk.Canvas):
    """Digitale staafmeters (PO, SWR, ALC, Id) met piekwaarde en getal."""

    ROWS = [("PO", 0, 100, (25, 50, 75, 100), "W"),
            ("SWR", 1.0, 3.0, (1.5, 2.0, 2.5, 3.0), ""),
            ("ALC", 0, 120, (60, 120), ""),
            ("Id", 0, 25, (5, 10, 15, 20, 25), "A")]

    def __init__(self, parent, w=360):
        self.rh = S(21)
        super().__init__(parent, width=w, height=self.rh * len(self.ROWS) + S(4),
                         bg=_mix(C["scr"], C["panel"], 0.5), highlightthickness=0)
        self.w = w
        self.vals = {r[0]: None for r in self.ROWS}
        self.peak = {r[0]: 0.0 for r in self.ROWS}
        self.x0, self.x1 = S(44), w - S(74)
        q = self.rh / 21.0                        # schaal binnen een rij
        for i, (name, lo, hi, ticks, unit) in enumerate(self.ROWS):
            y = S(4) + i * self.rh
            self.create_text(S(10), y + 11 * q, text=name, anchor="w", fill=C["acc"], font=(UI, S(9), "bold"))
            self.create_rectangle(self.x0, y + 5 * q, self.x1, y + 17 * q, fill=C["scr"], outline=C["border"])
            for tk_ in ticks:
                x = self.x0 + (tk_ - lo) / (hi - lo) * (self.x1 - self.x0)
                self.create_line(x, y + 17 * q, x, y + 21 * q, fill=C["dim"])


    def set(self, name, v):
        self.vals[name] = v

    def draw(self):
        self.delete("dyn")
        q = self.rh / 21.0
        for i, (name, lo, hi, ticks, unit) in enumerate(self.ROWS):
            y = S(4) + i * self.rh
            v = self.vals[name]
            pk = self.peak[name]
            if v is not None:
                pk = max(v, pk - (hi - lo) * 0.01)
            else:
                pk = max(lo, pk - (hi - lo) * 0.03)
            self.peak[name] = pk
            if v is not None:
                f = min(max((v - lo) / (hi - lo), 0), 1)
                col = C["acc"]
                if name == "SWR" and v >= 2.0:
                    col = C["red"]
                elif name == "SWR" and v >= 1.5:
                    col = C["amber"]
                elif name == "ALC" and v > 120 * 0.9:
                    col = C["red"]
                self.create_rectangle(self.x0 + 1, y + 6 * q, self.x0 + 1 + f * (self.x1 - self.x0 - 2), y + 16 * q,
                                      fill=col, outline="", tags="dyn")
            fp = min(max((pk - lo) / (hi - lo), 0), 1)
            if fp > 0.005:
                xp = self.x0 + fp * (self.x1 - self.x0)
                self.create_line(xp, y + 5 * q, xp, y + 17 * q, fill="#ffffff", width=2, tags="dyn")
            if v is None:
                txt = "--"
            elif name == "SWR":
                txt = f"{v:.1f}"
            elif name == "ALC":
                txt = f"{v / 1.2:.0f}%"
            elif name == "Id":
                txt = f"{v:.1f} A"
            else:
                txt = f"{v:.0f} {unit}"
            self.create_text(self.w - S(10), y + 11 * q, text=txt, anchor="e", fill=C["text"],
                             font=(DIGIT, S(12), "bold"), tags="dyn")


def _interp(v, pts):
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if v <= x1:
            return y0 + (v - x0) * (y1 - y0) / (x1 - x0)
    return pts[-1][1]


# kalibratie volgens de CI-V-referentie van de IC-7300 (waarden bij benadering)
def po_from_raw(v):        # 15 11: 0000=0%, 0143=50%, 0213=100%  (100 W)
    return _interp(v, [(0, 0.0), (143, 50.0), (213, 100.0), (255, 120.0)])


def id_from_raw(v):        # 15 16: 0000=0A, 0097=10A, 0146=15A, 0241=25A
    return _interp(v, [(0, 0.0), (97, 10.0), (146, 15.0), (241, 25.0), (255, 26.5)])


def vd_from_raw(v):        # 15 15: 0000=0V, 0013=10V, 0241=16V
    return _interp(v, [(0, 0.0), (13, 10.0), (241, 16.0), (255, 16.4)])


class SMeter(tk.Canvas):
    def __init__(self, parent, w=360, h=160, title="SIGNAL"):
        super().__init__(parent, width=w, height=h, bg=_mix(C["scr"], C["panel"], 0.5), highlightthickness=0)
        self.w, self.h = w, h
        self.cx, self.cy, self.r = w / 2, h - S(10), min(w / 2 - S(40), h - S(20))
        self.val = 0.0
        self.target = 0.0
        self.title = title
        self._static()

    def _xy(self, raw, r):
        a = math.radians(150 - min(max(raw, 0), 241) / 241 * 120)
        return self.cx + r * math.cos(a), self.cy - r * math.sin(a)

    def _static(self):
        r = self.r
        for raw in range(0, 241, 3):
            x1, y1 = self._xy(raw, r)
            x2, y2 = self._xy(raw + 3, r)
            col = _mix(C["dim"], C["acc"], raw / 120) if raw < 120 else _mix("#ff7a4d", C["red"], (raw - 120) / 121)
            self.create_line(x1, y1, x2, y2, fill=col, width=5)
        marks = [(i * 120 / 9, str(i)) for i in (1, 3, 5, 7, 9)] + [(160, "+20"), (201, "+40"), (241, "+60")]
        for raw, t in marks:
            x1, y1 = self._xy(raw, r - S(4))
            x2, y2 = self._xy(raw, r - S(12))
            self.create_line(x1, y1, x2, y2, fill=C["text"], width=2)
            tx, ty = self._xy(raw, r - S(23))
            self.create_text(tx, ty, text=t, fill=C["red"] if "+" in t else C["text"], font=(UI, S(8)))
        self.create_text(S(12), S(10), text=self.title, anchor="nw", fill=C["acc"], font=(UI, S(9), "bold"))

    @staticmethod
    def s_text(raw):
        if raw <= 120:
            return f"S{int(round(raw / (120 / 9)))}"
        return f"S9+{int(round((raw - 120) / 121 * 60 / 5) * 5)}"

    def set(self, raw):
        self.target = raw

    def animate(self, swr_text=""):
        self.val += 0.35 * (self.target - self.val)
        self.delete("dyn")
        x, y = self._xy(self.val, self.r + 6)
        self.create_line(self.cx, self.cy, x, y, fill="#f2f6f8", width=2, tags="dyn")
        self.create_oval(self.cx - 6, self.cy - 6, self.cx + 6, self.cy + 6, fill=C["btn"],
                         outline=C["acc"], tags="dyn")
        self.create_text(self.w - S(12), S(10), text=self.s_text(self.target), anchor="ne",
                         fill=C["text"], font=(UI, S(13), "bold"), tags="dyn")
        if swr_text:
            self.create_text(self.w - S(12), S(32), text=swr_text, anchor="ne", fill=C["acc2"],
                             font=(UI, S(10), "bold"), tags="dyn")


def swr_from_raw(v):
    pts = [(0, 1.0), (48, 1.5), (80, 2.0), (120, 3.0), (255, 6.0)]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if v <= x1:
            return y0 + (v - x0) * (y1 - y0) / (x1 - x0)
    return 6.0


# ----------------------------------------------------------------- logboek (ADIF)
def adif_field(name, val):
    val = str(val or "").strip()
    return f"<{name}:{len(val)}>{val} " if val else ""


def append_qso(path, rec):
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write(f"{APP_NAME} logbook\n" + adif_field("ADIF_VER", "3.1.4") +
                    adif_field("PROGRAMID", "SSTVStation") + adif_field("PROGRAMVERSION", APP_VERSION) + "<EOH>\n")
        f.write("".join(adif_field(k, v) for k, v in rec.items()) + "<EOR>\n")


def read_adif(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = f.read()
    except FileNotFoundError:
        return []
    i = data.upper().find("<EOH>")
    if i >= 0:
        data = data[i + 5:]
    out, rec, pos = [], {}, 0
    tag = re.compile(r"<(\w+)(?::(\d+))?(?::\w)?>", re.I)
    while True:
        m = tag.search(data, pos)
        if not m:
            break
        name = m.group(1).upper()
        if name == "EOR":
            out.append(rec)
            rec = {}
            pos = m.end()
            continue
        n = int(m.group(2) or 0)
        rec[name] = data[m.end():m.end() + n]
        pos = m.end() + n
    return out



# ----------------------------------------------------------------- hoofdapplicatie
class App:
    AUDIO_RANGES = (2500, 3000, 4000)

    @property
    def AUDIO_FMAX(self):
        return float(self.cfg.get("audio_range", 3000))
    STEPS = [10, 50, 100, 500, 1000, 5000]

    def __init__(self, root):
        self.root = root
        self.cfg = self._load_cfg()
        self.lang = self.cfg.get("lang", "nl")
        C.update(THEMES.get(self.cfg.get("theme", "Amber"), THEMES["Amber"]))
        self.civ = None
        self.audio = None
        self.gq = queue.Queue()
        self.freq = 0
        self.mode = ""
        self.mode_code = 0x01
        self.data = False
        self.filt = 1
        self.ifw = None
        self.tx = False
        self.smeter = 0
        self.swr = None
        self.scope = {"start": 0, "end": 0, "data": bytearray()}
        self.wf_floor = None
        self.scope_frames = 0
        self.poll_n = 0
        self.func_state = {"nb": 0, "nr": 0, "pamp": 0, "att": 0, "atu": 0}
        self.radio_addr = None
        self.radio_name = "IC-7300"
        self.detecting = False
        self.tx_meters = {"PO": None, "SWR": None, "ALC": None, "Id": None}
        self.vd = None
        self.update_info = None
        self._silent_since = None
        self.tool_tab = "dec"
        self.sender = SSTVSender(self)
        self.rx_img = None            # laatst getoonde RX-beeld (numpy)
        self.rx_name = ""
        self.last_rx = None           # laatst volledig ontvangen beeld (voor inzet / antwoord)
        self.tx_src = None            # PIL-beeld als achtergrond voor TX
        self.tx_img = None            # samengesteld zendbeeld (numpy)
        self.gallery = []             # [(pad, modusnaam, tijd)]
        self._thumbs = {}
        self._rx_dirty = True
        self._rx_t = 0
        st_cfg = self.cfg.get("step", 100)
        self.step_idx = self.STEPS.index(st_cfg) if st_cfg in self.STEPS else 2
        self._init_vars()
        self._load_tx_src(self.cfg.get("tx_image", ""), quiet=True)
        self._load_gallery()
        global SCALE
        try:
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            SCALE = scale_for(sw * 0.98, sh * 0.90)
        except tk.TclError:
            pass
        self._style()
        self._build()
        self._resize_job = None
        root.bind("<Configure>", self._on_root_configure, add="+")
        self.root.after(40, self._tick)
        self.root.after(250, self._poll)
        self.root.after(500, self._clock)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

    def t(self, nl, en):
        return nl if self.lang == "nl" else en

    # ---------- config
    def _load_cfg(self):
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
        except OSError:
            pass
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    user = json.load(f)
                cfg.update({k: v for k, v in user.items() if k != "templates"})
                cfg["templates"].update(user.get("templates", {}))
            except Exception as e:
                print("config:", e)
        if cfg["theme"] not in THEMES:
            cfg["theme"] = "Amber"
        if cfg.get("tx_mode") not in sstv.MODE_BY_KEY:
            cfg["tx_mode"] = "M1"
        return cfg

    def _save_cfg(self):
        try:
            self.cfg.update(port=self.port_var.get(), baud=int(self.baud_var.get()),
                            civ_addr=self.addr_var.get(), audio_device=self.dev_var.get(),
                            audio_out=self.out_var.get(), step=self.STEPS[self.step_idx], lang=self.lang,
                            tx_level=int(self.lvl_var.get()), inset=bool(self.inset_var.get()),
                            slant=bool(self.slant_var.get()))
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print("config save:", e)

    # ---------- variabelen & stijl
    def _init_vars(self):
        self.port_var = tk.StringVar(value=self.cfg["port"])
        self.baud_var = tk.StringVar(value=str(self.cfg["baud"]))
        self.addr_var = tk.StringVar(value=self.cfg["civ_addr"])
        self.dev_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.scope_var = tk.BooleanVar(value=True)
        self.range_var = tk.DoubleVar(value=45)
        self.ref_var = tk.DoubleVar(value=3)
        self.af_var = tk.DoubleVar(value=0)
        self.rf_var = tk.DoubleVar(value=255)
        self.pwr_var = tk.DoubleVar(value=255)
        self.lvl_var = tk.DoubleVar(value=float(self.cfg.get("tx_level", 50)))
        self.upd_var = tk.BooleanVar(value=bool(self.cfg.get("check_updates", True)))
        self.scope_gain = tk.DoubleVar(value=float(self.cfg.get("scope_gain", 0.55)))
        self.inset_var = tk.BooleanVar(value=bool(self.cfg.get("inset", False)))
        self.slant_var = tk.BooleanVar(value=bool(self.cfg.get("slant", True)))
        self.fentry = tk.StringVar()
        self.call_var = tk.StringVar()
        self.rst_var = tk.StringVar(value="595")
        self.rstr_var = tk.StringVar(value="595")
        self.hisname_var = tk.StringVar()
        self.tx_var = tk.StringVar(value=self.cfg["templates"]["F1"][1])
        self.txmode_var = tk.StringVar(value=sstv.MODE_BY_KEY[self.cfg["tx_mode"]].name)
        rxm = self.cfg.get("rx_mode", "auto")
        self.rxmode_var = tk.StringVar(value=sstv.MODE_BY_KEY[rxm].name if rxm in sstv.MODE_BY_KEY else "Auto")

    def _style(self):
        r = self.root
        r.configure(bg=C["bg"])
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("TCombobox", fieldbackground=C["panel2"], background=C["btn"], foreground=C["text"],
                     arrowcolor=C["acc"], bordercolor=C["border"], lightcolor=C["panel2"],
                     darkcolor=C["panel2"], selectbackground=C["panel2"], selectforeground=C["text"])
        st.map("TCombobox", fieldbackground=[("readonly", C["panel2"])], foreground=[("readonly", C["text"])])
        st.configure("Vertical.TScrollbar", background=C["btn"], troughcolor=C["panel"],
                     bordercolor=C["panel"], arrowcolor=C["acc"])
        st.configure("Treeview", background=C["scr"], fieldbackground=C["scr"], foreground=C["text"],
                     rowheight=S(24), borderwidth=0, font=(UI, S(10)))
        st.map("Treeview", background=[("selected", C["btnhi"])], foreground=[("selected", C["acc"])])
        st.configure("Treeview.Heading", background=C["panel2"], foreground=C["dim"], borderwidth=0,
                     font=(UI, S(9), "bold"), relief="flat")
        st.map("Treeview.Heading", background=[("active", C["btn"])])
        st.configure("Gallery.Treeview", rowheight=S(58), font=(UI, S(9)))
        r.option_add("*TCombobox*Listbox.background", C["panel2"])
        r.option_add("*TCombobox*Listbox.foreground", C["text"])
        r.option_add("*TCombobox*Listbox.selectBackground", C["acc"])
        r.option_add("*TCombobox*Listbox.selectForeground", "#03141c")

    # ---------- UI
    def _build(self):
        r = self.root
        t = self.t
        r.title(f"{APP_NAME} - Icom IC-7300" + (f" - {self.cfg['mycall']}" if self.cfg.get("mycall") else ""))

        # ===== kopbalk
        hdr = tk.Frame(r, bg=C["bg"])
        hdr.pack(fill="x", padx=14, pady=(10, 6))
        logo = tk.Canvas(hdr, width=S(54), height=S(54), bg=C["bg"], highlightthickness=0)
        logo.pack(side="left")
        logo.create_oval(S(2), S(2), S(52), S(52), outline=C["acc"], width=2, fill=C["panel"])
        logo.create_text(S(27), S(21), text="SSTV", fill=C["acc"], font=(UI, S(11), "bold"))
        for i, col in enumerate(("#ffffff", "#ffe02e", "#2ee6e6", "#4be38a", "#e04bff", "#ff4d5e")):
            logo.create_rectangle(S(13 + i * 5), S(31), S(18 + i * 5), S(41), fill=col, outline="")
        tt = tk.Frame(hdr, bg=C["bg"])
        tt.pack(side="left", padx=10)
        tk.Label(tt, text=APP_NAME, bg=C["bg"], fg=C["text"], font=(UI, S(22), "bold")).pack(anchor="w")
        sub = t("voor Icom IC-7300 / IC-7300MK2  ·  ontvangen & zenden",
                "for Icom IC-7300 / IC-7300MK2  ·  receive & transmit")
        if self.cfg.get("mycall"):
            sub += "  ·  " + self.cfg["mycall"].upper()
        tk.Label(tt, text=sub, bg=C["bg"], fg=C["dim"], font=(UI, S(10))).pack(anchor="w")
        srow = tk.Frame(tt, bg=C["bg"])
        srow.pack(anchor="w")
        self.status = label(srow, "", fg=C["amber"], font=(UI, S(9)), bg=C["bg"])
        self.status.pack(side="left")
        self.status.bind("<Button-1>", self._status_click)
        self.upd_btn = FlatButton(srow, "", command=self._open_update, padx=10, pady=1,
                                  font=(UI, S(9), "bold"), accent=C["acc2"])
        if self.update_info:
            self._show_update_button()

        con = tk.Frame(hdr, bg=C["bg"])
        con.pack(side="right")
        row = tk.Frame(con, bg=C["bg"])
        row.pack(anchor="e")
        label(row, t("Poort", "Port")).pack(side="left")
        entry(row, self.port_var, 6).pack(side="left", padx=(3, 8))
        label(row, "Baud").pack(side="left")
        ttk.Combobox(row, textvariable=self.baud_var, width=7, state="readonly",
                     values=["115200", "57600", "38400", "19200", "9600"]).pack(side="left", padx=(3, 8))
        label(row, "CI-V").pack(side="left")
        ttk.Combobox(row, textvariable=self.addr_var, width=5, values=["auto", "94", "B6"]).pack(side="left", padx=(3, 8))
        self.btn_conn = FlatButton(row, "", command=self.toggle_civ, padx=16, pady=6)
        self.btn_conn.pack(side="left")
        row2 = tk.Frame(con, bg=C["bg"])
        row2.pack(anchor="e", pady=(6, 0))
        label(row2, "RX").pack(side="left")
        self.dev_box = ttk.Combobox(row2, textvariable=self.dev_var, width=28, state="readonly",
                                    values=self._audio_devices("input"),
                                    postcommand=lambda: self._wide_popdown(self.dev_box))
        self.dev_box.pack(side="left", padx=(3, 6))
        label(row2, "TX").pack(side="left")
        self.out_box = ttk.Combobox(row2, textvariable=self.out_var, width=28, state="readonly",
                                    values=self._audio_devices("output"),
                                    postcommand=lambda: self._wide_popdown(self.out_box))
        self.out_box.pack(side="left", padx=(3, 8))
        self.dev_box.bind("<<ComboboxSelected>>", lambda e: self._dev_chosen("audio_device_manual"))
        self.out_box.bind("<<ComboboxSelected>>", lambda e: self._dev_chosen("audio_out_manual"))
        self._preselect_device()
        self.btn_audio = FlatButton(row2, "", command=self.toggle_audio, padx=14, pady=4)
        self.btn_audio.pack(side="left")
        row3 = tk.Frame(con, bg=C["bg"])
        row3.pack(anchor="e", pady=(6, 0))
        FlatButton(row3, t("Logboek", "Logbook"), command=self.show_log, padx=12, pady=3).pack(side="left", padx=(8, 0))
        FlatButton(row3, t("Ontvangen map", "Received folder"), command=self.open_rx_dir, padx=12, pady=3).pack(side="left", padx=(6, 0))
        FlatButton(row3, t("Enquête", "Survey"), command=self.open_survey, padx=12, pady=3).pack(side="left", padx=(6, 0))
        FlatButton(row3, t("Handleiding", "Manual"), command=self.open_manual, padx=12, pady=3).pack(side="left", padx=(6, 0))
        FlatButton(row3, "⚙ " + t("Instellingen", "Settings"), command=self.show_settings,
                   padx=12, pady=3).pack(side="left", padx=(6, 0))
        clk = tk.Frame(hdr, bg=C["bg"])
        clk.pack(side="left", padx=(30, 0))
        for name, attr in ((t("LOKAAL", "LOCAL"), "clk_loc"), ("UTC", "clk_utc")):
            col = tk.Frame(clk, bg=C["bg"])
            col.pack(side="left", padx=10)
            label(col, name, fg=C["acc"], font=(UI, S(8), "bold")).pack()
            lb = tk.Label(col, text="--:--:--", bg=C["bg"], fg=C["text"], font=(MONO, S(17), "bold"), width=8)
            lb.pack()
            setattr(self, attr, lb)

        # ===== hoofdgedeelte
        main = tk.Frame(r, bg=C["bg"])
        main.pack(fill="both", expand=True, padx=14)
        left = tk.Frame(main, bg=C["bg"])
        left.pack(side="left", fill="y")
        right = tk.Frame(main, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        # --- frequentie
        f, b, _ = panel(left)
        f.pack(fill="x")
        self.freq_lbl = tk.Label(b, text=self._freq_text(), bg=C["panel"], fg="#f4fbff",
                                 font=(MONO, S(30), "bold"), width=10, cursor="sb_v_double_arrow")
        self.freq_lbl.pack()
        self.freq_lbl.bind("<MouseWheel>", lambda e: self._step_tune(1 if e.delta > 0 else -1))
        fr = tk.Frame(b, bg=C["panel"])
        fr.pack(fill="x", pady=(2, 0))
        self.mode_lbl = label(fr, "--", fg=C["acc"], font=(UI, S(13), "bold"))
        self.mode_lbl.pack(side="left")
        self.band_lbl = label(fr, "", fg=C["dim"], font=(UI, S(11)))
        self.band_lbl.pack(side="left", padx=10)
        FlatButton(fr, t("Zet", "Set"), command=self.set_freq_khz, padx=8, pady=2, font=(UI, S(9), "bold")).pack(side="right")
        e = entry(fr, self.fentry, 9)
        e.pack(side="right", padx=4)
        e.bind("<Return>", lambda ev: self.set_freq_khz())
        label(fr, "kHz").pack(side="right")

        # --- knop + stap
        f, b, _ = panel(left)
        f.pack(fill="x", pady=6)
        self.knob = Knob(b, S(112), self._step_tune)
        self.knob.pack(side="left")
        side = tk.Frame(b, bg=C["panel"])
        side.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.tx_lbl = tk.Label(side, text="RX", bg=C["green"], fg="#03141c", font=(UI, S(14), "bold"), width=5, pady=1)
        self.tx_lbl.pack(pady=(2, 6))
        label(side, t("Afstemstap", "Tuning step")).pack()
        sr = tk.Frame(side, bg=C["panel"])
        sr.pack(pady=2)
        FlatButton(sr, "‹", command=lambda: self._chg_step(-1), padx=9, pady=2).pack(side="left")
        self.step_lbl = label(sr, "", fg=C["text"], font=(UI, S(13), "bold"), width=7)
        self.step_lbl.pack(side="left")
        FlatButton(sr, "›", command=lambda: self._chg_step(1), padx=9, pady=2).pack(side="left")
        self._chg_step(0)
        label(side, t("slepen of scrollen", "drag or scroll"), font=(UI, S(8))).pack(pady=(4, 0))

        # --- S-meter
        f, b, _ = panel(left)
        f.pack(fill="x")
        self.meter = SMeter(b, S(360), S(104), t("SIGNAAL", "SIGNAL"))
        self.meter.pack()
        self.bars = BarMeter(b, S(360))
        self.bars.pack(pady=(6, 0))
        self.vd_lbl = label(b, "", fg=C["dim"], font=(UI, S(8)))
        self.vd_lbl.pack(anchor="e")

        # --- AF / RF / PWR
        f, b, _ = panel(left)
        f.pack(fill="x", pady=6)
        for name, var, cmd in (("AF", self.af_var, self._send_af), ("RF", self.rf_var, self._send_rf),
                               ("PWR", self.pwr_var, self._send_pwr)):
            rw = tk.Frame(b, bg=C["panel"])
            rw.pack(fill="x", pady=0)
            label(rw, name, fg=C["acc"], font=(UI, S(10), "bold"), width=4).pack(side="left")
            Slider(rw, var, 0, 255, command=cmd, width=S(240), height=S(20)).pack(side="left", fill="x", expand=True)
            vl = label(rw, "", fg=C["text"], width=6)
            vl.pack(side="left")
            if name == "PWR":
                upd = (lambda v=var, l=vl: l.config(text=f"{v.get() / 2.55:.0f} W"))
            else:
                upd = (lambda v=var, l=vl: l.config(text=f"{int(v.get() / 2.55)}%"))
            trace(vl, var, upd)
            upd()

        # --- SSTV-hulp: decoderstatus, modi, frequenties
        f, b, head = panel(left, "")
        f.pack(fill="both", expand=True)
        head.title.config(text=t("SSTV-HULP", "SSTV TOOLS"))
        self.tool_btns = {}
        for key, txt in (("freq", t("Freq.", "Freq.")), ("modes", t("Modi", "Modes")), ("dec", "Decoder")):
            bt = FlatButton(head, txt, command=lambda k=key: self._tool_tab(k), padx=8, pady=1, font=(UI, S(8), "bold"))
            bt.pack(side="right", padx=1)
            self.tool_btns[key] = bt
        self.tool_body = b
        self._tool_tab(self.tool_tab)

        # ===== rechterkolom
        f, b, _ = panel(right)
        f.pack(fill="x")
        self.fil_btns = {}
        for i in (1, 2, 3):
            bt = FlatButton(b, f"FIL{i}", command=lambda i=i: self.set_filter(i), width=5, pady=6)
            bt.pack(side="left", padx=2)
            self.fil_btns[i] = bt
        tk.Frame(b, bg=C["border"], width=2, height=28).pack(side="left", padx=9)
        label(b, "IF").pack(side="left", padx=(0, 4))
        self.ifw_btns = {}
        for w in (1800, 2400, 2700, 3000):
            bt = FlatButton(b, f"{w / 1000:.1f}k", command=lambda w=w: self.set_ifw(w), width=3, pady=6, padx=8,
                            font=(UI, S(9), "bold"))
            bt.pack(side="left", padx=2)
            self.ifw_btns[w] = bt
        self.ifw_lbl = label(b, "", fg=C["text"], font=(UI, S(10), "bold"), width=7)
        self.ifw_lbl.pack(side="left", padx=4)
        tk.Frame(b, bg=C["border"], width=2, height=28).pack(side="left", padx=9)
        self.func_btns = {}
        for key, txt in (("nb", "NB"), ("nr", "NR"), ("pamp", "P.AMP"), ("att", "ATT")):
            bt = FlatButton(b, txt, command=lambda k=key: self.toggle_func(k), width=5, pady=6)
            bt.pack(side="left", padx=2)
            self.func_btns[key] = bt
        FlatButton(b, "SCOPE", var=self.scope_var, command=self.apply_scope, width=5, pady=6).pack(side="right", padx=2)
        tk.Frame(b, bg=C["border"], width=2, height=28).pack(side="right", padx=9)
        FlatButton(b, "TUNE", command=self.atu_tune, width=5, pady=6, accent=C["red"]).pack(side="right", padx=2)
        bt = FlatButton(b, "ATU", command=lambda: self.toggle_func("atu"), width=4, pady=6)
        bt.pack(side="right", padx=2)
        self.func_btns["atu"] = bt

        # --- radio-scope
        f, b, head = panel(right, "")
        f.pack(fill="x", pady=6)
        self.scope_title = head.title
        Slider(head, self.scope_gain, 0.2, 1.0, width=S(80), height=S(16)).pack(side="right")
        label(head, t("Contrast", "Contrast")).pack(side="right", padx=(10, 4))
        self.wf_scope = Waterfall(b, S(58), S(84), on_click=self._scope_click, label_fn=self._scope_label)
        self.wf_scope.canvas.pack(fill="x")

        # --- audio
        f, b, head = panel(right, t("AUDIO  ·  sync 1200 Hz  ·  beeld 1500–2300 Hz",
                                    "AUDIO  ·  sync 1200 Hz  ·  picture 1500–2300 Hz"))
        f.pack(fill="x")
        self.range_btns = {}
        for rg in self.AUDIO_RANGES:
            bt = FlatButton(head, f"{rg / 1000:g} kHz", command=lambda rg=rg: self._set_range(rg), padx=6, pady=0,
                            font=(UI, S(8), "bold"))
            bt.pack(side="left", padx=(6 if rg == self.AUDIO_RANGES[0] else 1, 0))
            bt.set_active(rg == int(self.AUDIO_FMAX))
            self.range_btns[rg] = bt
        for name, var, lo, hi in (("Ref", self.ref_var, -20, 30), ("Contrast", self.range_var, 15, 80)):
            Slider(head, var, lo, hi, width=S(70), height=S(16)).pack(side="right")
            label(head, name).pack(side="right", padx=(10, 4))
        self.aud_lbl = label(head, "", fg=C["acc"], font=(MONO, S(10), "bold"))
        self.aud_lbl.pack(side="right", padx=(10, 0))
        self.lvl = tk.Canvas(head, width=S(90), height=S(10), bg=C["scr"], highlightthickness=0)
        self.lvl.pack(side="right", padx=10)
        self.wf_audio = Waterfall(b, S(44), S(74), label_fn=lambda fr: f"{fr * self.AUDIO_FMAX:.0f}")
        self.wf_audio.canvas.pack(fill="x")

        # --- beelden: ontvangen, zenden, galerij
        low = tk.Frame(right, bg=C["bg"])
        low.pack(fill="both", expand=True, pady=6)
        low.columnconfigure(0, weight=1, uniform="img")
        low.columnconfigure(1, weight=1, uniform="img")
        low.rowconfigure(0, weight=1)
        f, b, head = panel(low, t("GALERIJ", "GALLERY"))
        f.grid(row=0, column=2, sticky="ns", padx=(8, 0))
        self.gal = ttk.Treeview(b, show="tree", style="Gallery.Treeview", selectmode="browse", height=4)
        self.gal.column("#0", width=S(190))
        gsb = ttk.Scrollbar(b, orient="vertical", command=self.gal.yview)
        self.gal.config(yscrollcommand=gsb.set)
        gsb.pack(side="right", fill="y")
        self.gal.pack(fill="both", expand=True)
        self.gal.bind("<<TreeviewSelect>>", self._gal_select)
        self.gal.bind("<Double-Button-1>", self._gal_open)
        self._fill_gallery()

        f, b, head = panel(low, "")
        f.grid(row=0, column=0, sticky="nsew")
        self.rx_title = head.title
        self.rx_canvas = tk.Canvas(b, bg=C["scr"], highlightthickness=0, height=S(220))
        self.rx_canvas.pack(fill="both", expand=True)
        rb = tk.Frame(b, bg=C["panel"])
        rb.pack(fill="x", pady=(6, 0))
        label(rb, "RX").pack(side="left", padx=(0, 4))
        rxbox = ttk.Combobox(rb, textvariable=self.rxmode_var, width=13, state="readonly",
                             values=["Auto"] + [m.name for m in sstv.MODES])
        rxbox.pack(side="left")
        rxbox.bind("<<ComboboxSelected>>", lambda e: self._apply_rx_opts())
        FlatButton(rb, "Slant", var=self.slant_var, command=self._apply_rx_opts, padx=8, pady=2,
                   font=(UI, S(9), "bold")).pack(side="left", padx=(6, 0))
        FlatButton(rb, "Stop", command=self.stop_rx, padx=8, pady=2, font=(UI, S(9), "bold"),
                   accent=C["red"]).pack(side="left", padx=(4, 0))
        self.rec_btn = FlatButton(rb, "● REC", command=self.toggle_rec, padx=8, pady=2, font=(UI, S(9), "bold"))
        self.rec_btn.pack(side="left", padx=(4, 0))
        FlatButton(rb, "WAV…", command=self.decode_wav, padx=8, pady=2,
                   font=(UI, S(9), "bold")).pack(side="left", padx=(4, 0))
        FlatButton(head, t("Als TX-beeld", "Use for TX"), command=self.rx_to_tx, padx=8, pady=1,
                   font=(UI, S(8), "bold")).pack(side="right")
        FlatButton(head, t("Opslaan…", "Save…"), command=self.save_rx_as, padx=8, pady=1,
                   font=(UI, S(8), "bold")).pack(side="right", padx=4)
        self.rx_canvas.bind("<Configure>", lambda e: self._draw_rx(force=True))

        f, b, head = panel(low, "")
        f.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.tx_title = head.title
        txbox = ttk.Combobox(head, textvariable=self.txmode_var, width=13, state="readonly",
                             values=[m.name for m in sstv.MODES])
        txbox.pack(side="right", padx=(4, 0))
        txbox.bind("<<ComboboxSelected>>", lambda e: self._tx_mode_changed())
        label(head, "TX").pack(side="right", padx=(10, 2))
        self.tx_canvas = tk.Canvas(b, bg=C["scr"], highlightthickness=0, height=S(220))
        self.tx_canvas.pack(fill="both", expand=True)
        self.tx_canvas.bind("<Configure>", lambda e: self._draw_tx())
        tb = tk.Frame(b, bg=C["panel"])
        tb.pack(fill="x", pady=(6, 0))
        FlatButton(tb, t("Beeld…", "Picture…"), command=self.choose_tx_image, padx=8, pady=2,
                   font=(UI, S(9), "bold")).pack(side="left")
        FlatButton(tb, t("Standaard", "Default"), command=self.clear_tx_image, padx=8, pady=2,
                   font=(UI, S(9), "bold")).pack(side="left", padx=4)
        FlatButton(tb, t("Inzet ontv.", "Inset RX"), var=self.inset_var, command=self.compose, padx=8, pady=2,
                   font=(UI, S(9), "bold")).pack(side="left")
        self.txinfo_lbl = label(tb, "", fg=C["dim"], font=(UI, S(9)))
        self.txinfo_lbl.pack(side="right")

        # ===== onderbalk
        bot = tk.Frame(r, bg=C["bg"])
        main.pack_forget()
        bot.pack(side="bottom", fill="x", padx=14, pady=(0, 12))
        main.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        f, b, _ = panel(bot)
        f.pack(side="left", fill="y")
        mr = tk.Frame(b, bg=C["panel"])
        mr.pack(fill="x")
        self.mode_btns = {}
        for name, code, dat in (("USB-D", 0x01, True), ("LSB-D", 0x00, True), ("USB", 0x01, False),
                                ("LSB", 0x00, False), ("FM", 0x05, False), ("AM", 0x02, False)):
            bt = FlatButton(mr, name, command=lambda c=code, d=dat: self.set_mode(c, d), width=5, pady=6,
                            font=(UI, S(11), "bold"))
            bt.pack(side="left", padx=2)
            self.mode_btns[name] = bt
        br = tk.Frame(b, bg=C["panel"])
        br.pack(fill="x", pady=(6, 0))
        self.band_btns = {}
        for name, fq in BANDS:
            bt = FlatButton(br, name, command=lambda fq=fq: self.set_band(fq), width=3, pady=6, padx=9,
                            font=(UI, S(11), "bold"))
            bt.pack(side="left", padx=2)
            self.band_btns[name] = bt

        f, b, _ = panel(bot)
        f.pack(side="left", fill="both", expand=True, padx=(10, 0))
        r1 = tk.Frame(b, bg=C["panel"])
        r1.pack(fill="x")
        label(r1, "Call").pack(side="left")
        ce = entry(r1, self.call_var, 11, font=(UI, S(11), "bold"))
        ce.pack(side="left", padx=(3, 3))
        ce.bind("<KeyRelease>", lambda e: self._compose_later())
        FlatButton(r1, "QRZ", command=self.qrz_lookup, padx=7, pady=1, font=(UI, S(8), "bold")).pack(side="left", padx=(0, 10))
        label(r1, "RSV snt").pack(side="left")
        re_ = entry(r1, self.rst_var, 4)
        re_.pack(side="left", padx=(3, 8))
        re_.bind("<KeyRelease>", lambda e: self._compose_later())
        label(r1, "RSV rcv").pack(side="left")
        entry(r1, self.rstr_var, 4).pack(side="left", padx=(3, 8))
        label(r1, t("Naam", "Name")).pack(side="left")
        entry(r1, self.hisname_var, 10).pack(side="left", padx=(3, 8))
        FlatButton(r1, "LOG QSO  Ctrl+L", command=self.log_qso, padx=10, pady=2,
                   font=(UI, S(9), "bold"), accent=C["green"]).pack(side="left")
        self.lvl_lbl = label(r1, "", fg=C["text"], width=5)
        self.lvl_lbl.pack(side="right")
        Slider(r1, self.lvl_var, 5, 100, width=S(120), height=S(18)).pack(side="right")
        label(r1, t("TX-niveau", "TX level")).pack(side="right", padx=(0, 4))
        trace(self.lvl_lbl, self.lvl_var, lambda: self.lvl_lbl.config(text=f"{self.lvl_var.get():.0f}%"))
        self.lvl_lbl.config(text=f"{self.lvl_var.get():.0f}%")
        r2 = tk.Frame(b, bg=C["panel"])
        r2.pack(fill="x", pady=6)
        for key in sorted(self.cfg["templates"], key=lambda k: int(k[1:])):
            lbl, _ = self.cfg["templates"][key]
            FlatButton(r2, f"{key}  {lbl}", command=lambda k=key: self.template(k), padx=6, pady=3,
                       font=(UI, S(9), "bold")).pack(side="left", padx=2, fill="x", expand=True)
            r.bind(f"<{key}>", lambda ev, k=key: self.template(k))
        r3 = tk.Frame(b, bg=C["panel"])
        r3.pack(fill="x")
        label(r3, t("Tekst", "Text")).pack(side="left", padx=(0, 4))
        self.tx_entry = entry(r3, self.tx_var, 20, font=(MONO, S(13)))
        self.tx_entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.tx_entry.bind("<Return>", lambda ev: self.compose())
        self.tx_entry.bind("<KeyRelease>", lambda e: self._compose_later())
        self.tx_entry.bind("<Control-Return>", lambda ev: self.send_image() or "break")
        FlatButton(r3, t("ZEND", "SEND") + "  Ctrl+⏎", command=self.send_image, padx=14, pady=4,
                   accent=C["acc"]).pack(side="left", padx=(8, 2))
        FlatButton(r3, "STOP  Esc", command=self.stop_tx, accent=C["red"], padx=14, pady=4).pack(side="left", padx=2)
        r.bind("<Escape>", lambda ev: self.stop_tx())
        r.bind("<Control-l>", lambda ev: self.log_qso())
        r.bind("<Control-L>", lambda ev: self.log_qso())

        self.compose()
        self._draw_rx(force=True)
        self._update_highlights()
        if not self.status.cget("text"):
            self._set_status(t("niet verbonden", "not connected"), C["amber"])

    def _on_root_configure(self, ev):
        if ev.widget is not self.root:
            return
        if self._resize_job:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(500, self._apply_scale)

    def _apply_scale(self):
        """Responsive: bij een duidelijk andere venstergrootte de interface opnieuw schalen."""
        global SCALE
        self._resize_job = None
        w, h = self.root.winfo_width(), self.root.winfo_height()
        if w < 200 or h < 200:
            return
        new = scale_for(w, h)
        if abs(new - SCALE) >= 0.08:
            SCALE = new
            self.rebuild()

    def rebuild(self):
        """UI opnieuw opbouwen (taal/thema/sjablonen), zonder verbinding te verbreken."""
        for w in self.root.winfo_children():
            if not isinstance(w, tk.Toplevel):
                w.destroy()
        C.update(THEMES.get(self.cfg.get("theme", "Amber"), THEMES["Amber"]))
        self.status = None
        self._thumbs = {}
        self._style()
        self._build()
        self._set_status(self.t("verbonden", "connected") if self.civ else self.t("niet verbonden", "not connected"),
                         C["green"] if self.civ else C["amber"])

    # ---------- kleine UI-helpers
    def _freq_text(self):
        f = self.freq
        if not f:
            return "--.---.---"
        return f"{f // 1000000:d}.{(f // 1000) % 1000:03d}.{f % 1000:03d}"

    def _chg_step(self, d):
        self.step_idx = min(max(self.step_idx + d, 0), len(self.STEPS) - 1)
        s = self.STEPS[self.step_idx]
        self.step_lbl.config(text=f"{s} Hz" if s < 1000 else f"{s // 1000} kHz")

    def _step_tune(self, d):
        if self.freq:
            st = self.STEPS[self.step_idx]
            f = self.freq
            self.set_freq((f // st + 1) * st if d > 0 else ((f - 1) // st) * st)

    def _mode_text(self):
        if not self.mode:
            return ""
        return self.mode + ("-D" if self.data and self.mode in ("USB", "LSB", "AM", "FM") else "")

    def _update_highlights(self):
        t = self.t
        mt = self._mode_text()
        for k, bt in self.mode_btns.items():
            bt.set_active(k == mt)
        band = ""
        for k, (lo, hi) in BAND_RANGES.items():
            if lo <= self.freq <= hi:
                band = k
        for k, bt in self.band_btns.items():
            bt.set_active(k == band)
        self.band_lbl.config(text=f"{band} m" if band else "")
        for i, bt in self.fil_btns.items():
            bt.set_active(self.civ is not None and i == self.filt)
        for k, bt in self.func_btns.items():
            bt.set_active(bool(self.func_state.get(k)))
        for w, bt in self.ifw_btns.items():
            bt.set_active(self.civ is not None and w == self.ifw)
        self.ifw_lbl.config(text=f"{self.ifw} Hz" if (self.civ and self.ifw) else "")
        if self.civ:
            if self.detecting:
                self.btn_conn.config(text="●  " + t("zoeken…", "searching…"))
                self.btn_conn.set_active(True, C["amber"])
            else:
                self.btn_conn.config(text="●  " + t(f"{self.radio_name} verbonden", f"{self.radio_name} connected"))
                self.btn_conn.set_active(True, C["green"])
        else:
            self.btn_conn.set_active(False)
            self.btn_conn.config(text="●  " + t("Verbind radio", "Connect radio"), fg=C["red"])
        self.btn_audio.set_active(self.audio is not None, C["green"])
        self.btn_audio.config(text=t("Audio aan", "Audio on") if self.audio else t("Start audio", "Start audio"))
        if self.mode:
            self.mode_lbl.config(text=f"{mt}   FIL{self.filt}",
                                 fg=C["acc"] if self.data else C["amber"])
        self.freq_lbl.config(text=self._freq_text())

    def _clock(self):
        try:
            self.clk_loc.config(text=time.strftime("%H:%M:%S"))
            self.clk_utc.config(text=time.strftime("%H:%M:%S", time.gmtime()))
        except tk.TclError:
            pass
        self.root.after(1000 - int(time.time() * 1000) % 1000 + 5, self._clock)

    def _set_status(self, text, color=None):
        if self.status is not None:
            self.status.config(text=text, fg=color or C["dim"])

    # ---------- audio-apparaten
    def _audio_devices(self, kind):
        """Korte, leesbare lijst: elk apparaat één keer (via MME, zoals Windows het toont),
        de USB Audio CODEC van de radio bovenaan.  Het apparaatnummer zit in self._devmap."""
        if not hasattr(self, "_devmap"):
            self._devmap = {}
        m = self._devmap[kind] = {}
        if sd is None:
            return []
        key = "max_input_channels" if kind == "input" else "max_output_channels"
        try:
            apis = sd.query_hostapis()
            devs = [(i, d) for i, d in enumerate(sd.query_devices()) if d[key] > 0]
            mme = [(i, d) for i, d in devs if apis[d["hostapi"]]["name"] == "MME"]
            use = mme or devs
            for i, d in use:
                name = d["name"].strip()
                if not mme:
                    name += f"  [{apis[d['hostapi']]['name']}]"
                if name.lower().startswith(("microsoft geluidstoewijzing", "microsoft sound mapper",
                                            "primaire", "primary")):
                    continue
                if name not in m:
                    m[name] = i
        except Exception as e:
            print("audio:", e)
        return sorted(m, key=lambda n: (0 if "CODEC" in n.upper() else 1, n.lower()))

    @staticmethod
    def _dev_key(name):
        """Naam zonder nummer en API-aanduiding (ook voor instellingen van een oudere versie)."""
        name = re.sub(r"^\d+:\s*", "", name or "")
        name = re.sub(r"\s*\[[^\]]*\]\s*$", "", name)
        return name.strip().lower()

    def _dev_index(self, kind, value):
        m = getattr(self, "_devmap", {}).get(kind, {})
        if value in m:
            return m[value]
        k = self._dev_key(value)
        for n, i in m.items():
            if self._dev_key(n) == k:
                return i
        raise ValueError(self.t(f"audioapparaat niet gevonden: {value}", f"audio device not found: {value}"))

    def _wide_popdown(self, box):
        """Uitklaplijst breder en hoger maken dan het invoerveld, zodat de namen leesbaar zijn."""
        try:
            pd = str(box.tk.call("ttk::combobox::PopdownWindow", box))
            box.tk.call(f"{pd}.f.l", "configure", "-height", min(12, max(1, len(box["values"]))))
            fnt = tkfont.nametofont(str(box.tk.call(f"{pd}.f.l", "cget", "-font")))
            want = max([fnt.measure(v) for v in box["values"]] + [0]) + S(40)

            def widen():
                try:
                    if not int(box.tk.call("winfo", "ismapped", pd)):
                        return
                    w = max(want, box.winfo_width())
                    x = min(box.winfo_rootx(), box.winfo_screenwidth() - w - 4)
                    y = box.winfo_rooty() + box.winfo_height()
                    h = int(box.tk.call("winfo", "reqheight", pd))
                    box.tk.call("wm", "geometry", pd, f"{w}x{h}+{max(0, x)}+{y}")
                except tk.TclError:
                    pass
            box.after(1, widen)
            box.after(30, widen)
        except tk.TclError:
            pass

    def _dev_chosen(self, key):
        self.cfg[key] = True                             # door de gebruiker gekozen: niet meer overschrijven
        self._save_cfg()

    def _preselect_device(self):
        """Kies de USB Audio CODEC van de radio. Een apparaat dat de gebruiker zelf koos, blijft staan;
        een automatisch gekozen ander apparaat (bijv. omdat de radio uit stond) wordt vervangen door de CODEC."""
        for box, var, key in ((self.dev_box, self.dev_var, "audio_device"),
                              (self.out_box, self.out_var, "audio_out")):
            devs = list(box["values"])
            want = self.cfg.get(key, "")
            manual = bool(self.cfg.get(key + "_manual"))
            codec = next((d for d in devs if "CODEC" in d.upper() and "MME" in d.upper()), None) or \
                next((d for d in devs if "CODEC" in d.upper()), None)

            def by_name(name):
                k = self._dev_key(name)
                return next((d for d in devs if k and self._dev_key(d) == k), None)
            if manual and want:
                pick = want if want in devs else by_name(want)
                if pick:
                    var.set(pick)
                    continue
            cur = var.get()
            if codec and "CODEC" not in cur.upper():
                var.set(codec)
            elif cur in devs:
                continue
            elif want and (want in devs or by_name(want)):
                var.set(want if want in devs else by_name(want))
            elif codec:
                var.set(codec)
            elif devs:
                var.set(devs[0])

    # ---------- verbinding
    def toggle_civ(self, quiet=False):
        if self.civ:
            self._disconnect()
            self._user_off_civ = True                    # handmatig uit: niet automatisch opnieuw
            return
        self._user_off_civ = False
        if serial is None:
            if not quiet:
                messagebox.showerror("Error", "pyserial missing:  pip install pyserial")
            return
        txt = self.addr_var.get().strip().lower().replace("h", "")
        auto = txt in ("", "auto")
        try:
            addr = 0x94 if auto else int(txt, 16)
            self.civ = CIV(self.port_var.get().strip(), int(self.baud_var.get()), addr, self._on_frame)
        except Exception as e:
            self.civ = None
            if quiet:
                self._set_status(self.t(f"{self.port_var.get()} niet beschikbaar – staat de radio aan? "
                                        "(opnieuw proberen…)",
                                        f"{self.port_var.get()} not available – is the radio on? (retrying…)"),
                                 C["amber"])
            else:
                messagebox.showerror(self.t("Seriële poort", "Serial port"),
                                     self.t(f"Kan {self.port_var.get()} niet openen:\n{e}",
                                            f"Cannot open {self.port_var.get()}:\n{e}"))
            return
        self.civ.send_to(addr, 0x1C, b"\x00\x00")           # zeker weten: ontvangen (TX uit)
        if auto:
            self.civ.send_to(0xB6, 0x1C, b"\x00\x00")
            self.detecting = True
            self._probe()
            self._set_status(self.t("radio zoeken (94h / B6h)…", "looking for radio (94h / B6h)…"), C["amber"])
        else:
            self._radio_found(addr)
        self._update_highlights()

    def _probe(self):
        if self.civ and self.detecting:
            for a in RADIOS:
                self.civ.send_to(a, 0x19, b"\x00")
            self.root.after(2000, self._probe_timeout)

    def _probe_timeout(self):
        if self.civ and self.detecting:
            self._set_status(self.t("geen antwoord van de radio – controleer COM-poort, baud en CI-V-adres",
                                    "no answer from the radio – check COM port, baud rate and CI-V address"),
                             C["red"])
            self._probe()

    def _radio_found(self, addr):
        self.detecting = False
        self.radio_addr = addr
        self.radio_name = RADIOS.get(addr, f"Icom {addr:02X}h")
        self.civ.addr = addr
        self._set_status(f"{self.radio_name} via {self.port_var.get()} (CI-V {addr:02X}h)", C["green"])
        c = self.civ
        c.send(0x1C, b"\x00\x00")
        for cmd, data in ((0x03, b""), (0x04, b""), (0x1A, b"\x06"), (0x1A, b"\x03"), (0x14, b"\x01"),
                          (0x14, b"\x02"), (0x14, b"\x0A"), (0x16, b"\x22"), (0x16, b"\x40"),
                          (0x16, b"\x02"), (0x11, b""), (0x1C, b"\x01"), (0x15, b"\x15")):
            c.send(cmd, data)
        self.apply_scope()
        self._update_highlights()

    def _disconnect(self):
        if self.sender.busy:
            self.sender.stop()
        if self.civ:
            try:
                self.civ.send(0x1C, b"\x00\x00")
                self.civ.send(0x27, b"\x11\x00")
            except Exception:
                pass
            self.civ.close()
        self.civ = None
        self._set_status(self.t("niet verbonden", "not connected"), C["amber"])
        self._update_highlights()

    def apply_scope(self):
        if self.civ:
            if self.scope_var.get():
                self.civ.send(0x27, b"\x10\x01")
                self.civ.send(0x27, b"\x11\x01")
            else:
                self.civ.send(0x27, b"\x11\x00")

    def toggle_audio(self, quiet=False):
        self._rx_dirty = True                            # titel ONTVANGEN ("audio staat uit") bijwerken
        if self.audio:
            self.audio.close()
            self.audio = None
            self._user_off_audio = True
            self._update_highlights()
            return
        self._user_off_audio = False
        if sd is None:
            if not quiet:
                messagebox.showerror("Error", "sounddevice missing:  pip install sounddevice")
            return
        try:
            idx = self._dev_index("input", self.dev_var.get())
            self.audio = AudioEngine(idx, self._on_wf_row, self._make_decoder)
        except Exception as e:
            self.audio = None
            if not quiet:
                messagebox.showerror("Audio", self.t(f"Kan audio niet starten:\n{e}", f"Cannot start audio:\n{e}"))
            return
        self._apply_rx_opts()
        self._save_cfg()
        self._rx_dirty = True
        self._update_highlights()

    def autostart(self):
        """Bij het opstarten (en daarna elke 10 s zolang nodig): radio verbinden en audio starten.
        Wat de gebruiker zelf heeft uitgezet, blijft uit."""
        if not self.cfg.get("autostart", True) or getattr(self, "_quitting", False):
            return
        if self.civ is None and not getattr(self, "_user_off_civ", False):
            self.toggle_civ(quiet=True)
        if self.audio is None and not getattr(self, "_user_off_audio", False) and sd is not None \
                and not self.sender.busy:
            self._refresh_devices()
            # alleen starten op de USB Audio CODEC van de radio (of een zelf gekozen apparaat);
            # staat de radio uit, dan is de CODEC er nog niet en proberen we het straks opnieuw
            if self.dev_var.get() and ("CODEC" in self.dev_var.get().upper() or self.cfg.get("audio_device_manual")):
                self.toggle_audio(quiet=True)
        if (self.civ is None and not getattr(self, "_user_off_civ", False)) or \
                (self.audio is None and not getattr(self, "_user_off_audio", False)):
            self.root.after(10000, self.autostart)

    def _refresh_devices(self):
        """Apparatenlijst opnieuw ophalen (de USB-audio verschijnt pas als de radio aan staat)."""
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        self.dev_box["values"] = self._audio_devices("input")
        self.out_box["values"] = self._audio_devices("output")
        self._preselect_device()

    def _make_decoder(self, fs):
        return sstv.SSTVDecoder(fs, lambda *ev: self.gq.put(("rxev",) + ev))

    def _apply_rx_opts(self):
        name = self.rxmode_var.get()
        m = next((m for m in sstv.MODES if m.name == name), None)
        self.cfg["rx_mode"] = m.key if m else "auto"
        self.cfg["slant"] = bool(self.slant_var.get())
        if self.audio:
            d = self.audio.decoder
            d.set_forced(m)
            d.slant = bool(self.slant_var.get())
            d.freerun = bool(self.cfg.get("freerun", True))
            d.set_filter(self.cfg.get("rx_filter", "normal"))
            d.denoise = bool(self.cfg.get("denoise", True))

    def _set_rx_filter(self, name):
        self.cfg["rx_filter"] = name
        for k, bt in getattr(self, "filt_btns", {}).items():
            bt.set_active(k == name)
        self._apply_rx_opts()
        self._save_cfg()

    def _toggle_denoise(self):
        self.cfg["denoise"] = not self.cfg.get("denoise", True)
        if getattr(self, "dn_btn", None):
            self.dn_btn.set_active(self.cfg["denoise"])
        self._apply_rx_opts()
        self._rx_dirty = True
        self._save_cfg()

    # ---------- CI-V ontvangst (reader-thread)
    def _on_frame(self, cmd, data, frm=None):
        if self.detecting and frm is not None and frm != CTRL_ADDR:
            self.gq.put(("found", frm))
            if cmd == 0x19:
                return
        if cmd in (0x00, 0x03) and len(data) >= 5:
            self.gq.put(("freq", bcd_le_to_int(data[:5])))
        elif cmd in (0x01, 0x04) and len(data) >= 1:
            self.gq.put(("mode", data[0], data[1] if len(data) > 1 else None))
        elif cmd == 0x14 and len(data) >= 3:
            v = bcd_byte(data[1]) * 100 + bcd_byte(data[2])
            if data[0] == 0x01:
                self.gq.put(("af", v))
            elif data[0] == 0x02:
                self.gq.put(("rf", v))
            elif data[0] == 0x0A:
                self.gq.put(("pwr", v))
        elif cmd == 0x15 and len(data) >= 3:
            v = bcd_byte(data[1]) * 100 + bcd_byte(data[2])
            if data[0] == 0x02:
                self.smeter = v
            elif data[0] == 0x11:
                self.tx_meters["PO"] = po_from_raw(v)
            elif data[0] == 0x12:
                self.swr = swr_from_raw(v)
                self.tx_meters["SWR"] = self.swr
            elif data[0] == 0x13:
                self.tx_meters["ALC"] = float(v)
            elif data[0] == 0x15:
                self.vd = vd_from_raw(v)
            elif data[0] == 0x16:
                self.tx_meters["Id"] = id_from_raw(v)
        elif cmd == 0x16 and len(data) >= 2:
            key = {0x22: "nb", 0x40: "nr", 0x02: "pamp"}.get(data[0])
            if key:
                self.gq.put(("func", key, data[1]))
        elif cmd == 0x11 and len(data) >= 1:
            self.gq.put(("func", "att", 1 if data[0] else 0))
        elif cmd == 0x1A and len(data) >= 2 and data[0] == 0x03:
            idx = bcd_byte(data[1])
            self.gq.put(("ifw", (idx + 1) * 50 if idx <= 9 else 500 + (idx - 9) * 100))
        elif cmd == 0x1A and len(data) >= 2 and data[0] == 0x06:
            self.gq.put(("data", bool(data[1])))
        elif cmd == 0x1C and len(data) >= 2 and data[0] == 0x00:
            self.gq.put(("txstate", bool(data[1])))
        elif cmd == 0x1C and len(data) >= 2 and data[0] == 0x01:
            self.gq.put(("func", "atu", 1 if data[1] else 0))
        elif cmd == 0x27 and len(data) >= 4 and data[0] == 0x00:
            self._scope_frame(data)
        elif cmd == 0xFA:
            self.gq.put(("ng", None))

    def _scope_frame(self, data):
        self.scope_frames += 1

        def ok(i):
            if len(data) <= i + 1:
                return False
            q, m = bcd_byte(data[i]), bcd_byte(data[i + 1])
            return 1 <= q <= m <= 20
        o = 2 if ok(2) else (1 if ok(1) else None)
        if o is None:
            return
        seq, smax = bcd_byte(data[o]), bcd_byte(data[o + 1])
        h = o + 2
        s = self.scope
        if seq == 1:
            s["data"] = bytearray()
            if len(data) >= h + 11:
                fmode = data[h]
                f1 = bcd_le_to_int(data[h + 1:h + 6])
                f2 = bcd_le_to_int(data[h + 6:h + 11])
                if fmode == 0x00:
                    s["start"], s["end"] = f1 - f2, f1 + f2
                else:
                    s["start"], s["end"] = f1, f2
                s["data"] += data[h + 12:]
        else:
            s["data"] += data[h:]
        if seq == smax and len(s["data"]) > 10:
            arr = np.frombuffer(bytes(s["data"]), dtype=np.uint8).astype(np.float32) / 160.0
            self.gq.put(("scope", arr))

    def _on_wf_row(self, spec, fs):
        nb = int(self.AUDIO_FMAX / (fs / AudioEngine.FFT))
        self.gq.put(("awf", 20 * np.log10(spec[:nb] + 1e-9), fs))

    # ---------- GUI-tick
    def _set_tx(self, on):
        if on == self.tx:
            return
        self.tx = on
        self.tx_lbl.config(text="TX" if on else "RX", bg=C["red"] if on else C["green"])
        if self.audio:
            if on:
                self.audio.decoder.stop()               # lopende ontvangst afsluiten (deelbeeld bewaren)
            self.audio.decoder.muted = on
        if not on:
            self.swr = None
            self.tx_meters = {k: None for k in self.tx_meters}

    def _tick(self):
        try:
            self._tick_body()
        except tk.TclError as e:
            if "invalid command name" in str(e) or "application has been destroyed" in str(e):
                return
            log_error("gui", e)
        except Exception as e:
            if not getattr(self, "_tick_err", False):
                self._tick_err = True
                log_error("gui", e)
                self._set_status(self.t(f"interne fout: {e} (zie error.log)", f"internal error: {e} (see error.log)"),
                                 C["red"])
        self.root.after(40, self._tick)

    def _tick_body(self):
        hl = False
        t = self.t
        try:
            while True:
                item = self.gq.get_nowait()
                kind = item[0]
                if kind == "rxev":
                    self._rx_event(item[1:])
                elif kind == "tx":
                    self._set_tx(True)
                    self._set_status(t(f"zenden: {item[1]} ({item[2]:.0f} s)", f"transmitting: {item[1]} ({item[2]:.0f} s)"),
                                     C["red"])
                elif kind == "txdone":
                    self._set_tx(False)
                    if self.audio:
                        self.audio.decoder.muted = False
                    self._set_status(t("klaar met zenden", "transmission finished"), C["green"])
                    self._draw_tx()
                elif kind == "txerr":
                    messagebox.showerror(t("Zenden", "Transmit"), t(f"Zenden mislukt:\n{item[1]}",
                                                                    f"Transmission failed:\n{item[1]}"))
                elif kind == "txstate":
                    if not self.sender.busy:
                        self._set_tx(item[1])
                elif kind == "freq":
                    self.freq = item[1]
                    hl = True
                elif kind == "mode":
                    self.mode_code = item[1]
                    self.mode = MODES.get(item[1], f"{item[1]:02X}")
                    if item[2] in (1, 2, 3):
                        self.filt = item[2]
                    hl = True
                elif kind == "data":
                    self.data = item[1]
                    hl = True
                elif kind == "af":
                    self.af_var.set(item[1])
                elif kind == "rf":
                    self.rf_var.set(item[1])
                elif kind == "pwr":
                    self.pwr_var.set(item[1])
                elif kind == "update":
                    self.update_info = item[1]
                    self._show_update_button()
                    self._ask_update()
                elif kind == "wavdone":
                    self._set_status(t(f"{item[1]}: {item[2]} beeld(en) gevonden", f"{item[1]}: {item[2]} picture(s) found"),
                                     C["green"] if item[2] else C["amber"])
                elif kind == "upd_ready":
                    self._run_update(item[1], item[2])
                    return
                elif kind == "upd_fail":
                    self._set_status(self.t(f"update mislukt: {item[1]} – probeer de releasepagina",
                                            f"update failed: {item[1]} – try the release page"), C["red"])
                    webbrowser.open(self.update_info["url"])
                elif kind == "found":
                    if self.detecting and self.civ:
                        self._radio_found(item[1])
                elif kind == "ifw":
                    self.ifw = item[1]
                    hl = True
                elif kind == "func":
                    self.func_state[item[1]] = item[2]
                    if item[1] == "pamp":
                        self.func_btns["pamp"].config(text=("P.AMP", "P.AMP1", "P.AMP2")[min(item[2], 2)])
                    hl = True
                elif kind == "scope":
                    self._scope_row(item[1])
                elif kind == "awf":
                    self._audio_row(item[1], item[2])
                elif kind == "ng":
                    self._set_status(t("radio: NG (commando geweigerd)", "radio: NG (command rejected)"), C["red"])
                    self.root.after(3000, lambda: self._set_status(
                        t("verbonden", "connected") if self.civ else t("niet verbonden", "not connected"),
                        C["green"] if self.civ else C["amber"]))
        except queue.Empty:
            pass
        if hl:
            self._update_highlights()

        F = self.AUDIO_FMAX
        self.wf_audio.markers = [(1200 / F, C["red"], 0), (1500 / F, C["acc"], 0), (2300 / F, C["acc"], 0)]
        s = self.scope
        if s["end"] > s["start"] and self.freq:
            self.wf_scope.markers = [((self.freq - s["start"]) / (s["end"] - s["start"]), C["red"], 0)]
        now = time.time()
        if now - getattr(self, "_det_t", 0) > 0.5:
            self._det_t = now
            self._detect_sstv(now)
        d = self.audio.decoder if self.audio else None
        if d is not None and d.state == "rx" and d.mode is not None:
            self.wf_audio.labels = [(1750 / F, f"SSTV · {d.mode.name}", C["green"])]
        else:
            self.wf_audio.labels = []
        self.wf_scope.render()
        self.wf_audio.render()
        self.meter.set(self.smeter if not self.tx else 0)
        self.meter.animate(f"SWR {self.swr:.1f}" if (self.tx and self.swr) else "")
        for k, v in self.tx_meters.items():
            self.bars.set(k, v if self.tx else None)
        self.bars.draw()
        self.vd_lbl.config(text=f"Vd {self.vd:.1f} V" if self.vd else "")
        if now - getattr(self, "_lf_t", 0) > 1.0:
            self._lf_t = now
            n = self.scope_frames - getattr(self, "_lf_n", 0)
            self._lf_n = self.scope_frames
            base = t(f"SPECTRUMSCOPE {self.radio_name}  ·  SSTV wordt groen omkaderd – klik erop om af te stemmen",
                     f"SPECTRUM SCOPE {self.radio_name}  ·  SSTV is framed in green – click it to tune")
            if self.civ and self.scope_var.get():
                if n:
                    self.scope_title.config(text=f"{base}  ·  {n} frames/s", fg=C["dim"])
                else:
                    if self.radio_addr == 0xB6:
                        hint = t("SPECTRUMSCOPE  ·  GEEN scope-data: kies COM-poort A (CI-V), 115200 baud, scope aan op de radio",
                                 "SPECTRUM SCOPE  ·  NO scope data: use COM port A (CI-V), 115200 baud, scope on at the radio")
                    else:
                        hint = t("SPECTRUMSCOPE  ·  GEEN scope-data: zet CI-V USB Port = Unlink from [REMOTE] en 115200 baud",
                                 "SPECTRUM SCOPE  ·  NO scope data: set CI-V USB Port = Unlink from [REMOTE] and 115200 baud")
                    self.scope_title.config(text=hint, fg=C["amber"])
            else:
                self.scope_title.config(text=base, fg=C["dim"])
        self._audio_check(now)
        if self.audio and self.audio.rec is not None and self.audio.rec_full:
            self.toggle_rec()
        self._tool_refresh(now)
        if now - self._rx_t > 0.25:
            self._rx_t = now
            self._draw_rx()
            if self.sender.busy:
                self._draw_tx()
        if self.audio:
            d = self.audio.decoder
            lv = min(1.0, d.level * 4)
            self.lvl.delete("all")
            self.lvl.create_rectangle(0, 0, S(90) * lv, S(10), fill=C["acc"] if d.state == "rx" else C["btnhi"],
                                      outline="")
            st = d.status()
            self.aud_lbl.config(text=signed(st["offset"], "Hz") if st["state"] == "rx" and st["vis"] else "")

    # ---------- RX-beeld
    def _rx_event(self, ev):
        t = self.t
        kind = ev[0]
        if kind == "start":
            name, vis = ev[1], ev[2]
            self.rx_name = name
            self._rx_dirty = True
            self._set_status(t(f"ontvangen: {name}" + ("" if vis else " (zonder VIS)"),
                               f"receiving: {name}" + ("" if vis else " (no VIS)")), C["acc2"])
        elif kind == "done":
            img, name, complete, lines = ev[1], ev[2], ev[3], ev[4]
            self.rx_img, self.rx_name = img, name
            self._rx_dirty = True
            if lines >= max(16, img.shape[0] // 4):
                path = self._save_rx(img, name)
                self.last_rx = img
                if self.inset_var.get():
                    self.compose()
                self._set_status(t(f"{name} ontvangen" + ("" if complete else f" (deels, {lines} regels)") +
                                   " – opgeslagen", f"{name} received" + ("" if complete else f" (partial, {lines} lines)") +
                                   " – saved"), C["green"])
                if path:
                    self.gallery.insert(0, (path, name, time.time()))
                    self._fill_gallery()
            else:
                self._set_status(t(f"{name}: te weinig regels, niet opgeslagen", f"{name}: too few lines, not saved"),
                                 C["amber"])

    def _save_rx(self, img, name):
        if Image is None:
            return None
        try:
            os.makedirs(RX_DIR, exist_ok=True)
            fn = time.strftime("%Y-%m-%d_%H%M%SZ_", time.gmtime()) + re.sub(r"[^A-Za-z0-9]+", "", name) + ".png"
            path = os.path.join(RX_DIR, fn)
            Image.fromarray(img).save(path)
            return path
        except Exception as e:
            log_error("save", e)
            return None

    def _draw_rx(self, force=False):
        c = self.rx_canvas
        d = self.audio.decoder if self.audio else None
        live = d is not None and d.state == "rx" and d.img is not None
        if live:
            img = d.img
            if self.cfg.get("denoise", True) and img.shape[1] <= 512:
                img = sstv.denoise(img)                  # live ook ruisarm (grote PD-modi: pas aan het eind)
            st = d.status()
            title = self.t(f"ONTVANGEN  ·  {st['mode']}  ·  regel {st['line']}/{st['lines']}",
                           f"RECEIVING  ·  {st['mode']}  ·  line {st['line']}/{st['lines']}")
        elif not (force or self._rx_dirty):
            return
        else:
            img = self.rx_img
            title = self.t("ONTVANGEN  ·  wacht op signaal", "RECEIVED  ·  waiting for signal") if img is None else \
                self.t(f"ONTVANGEN  ·  {self.rx_name}", f"RECEIVED  ·  {self.rx_name}")
            if d is None:
                title += self.t("  ·  audio staat uit", "  ·  audio is off")
        self._rx_dirty = False
        self.rx_title.config(text=title)
        cw, ch = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        c.delete("all")
        if img is None:
            c.create_text(cw / 2, ch / 2, text=self.t("Nog geen beeld ontvangen", "No picture received yet"),
                          fill=C["dim"], font=(UI, S(11)))
            return
        w, h = fit_size(img.shape[1], img.shape[0], cw - 8, ch - 8)
        self._rx_photo = photo_from_array(sstv.resize_nearest(img, w, h) if w > img.shape[1] else
                                          sstv.resize_area(img, w, h))
        x0, y0 = (cw - w) // 2, (ch - h) // 2
        c.create_image(x0, y0, anchor="nw", image=self._rx_photo)
        if live:
            y = y0 + h * st["line"] / max(st["lines"], 1)
            c.create_line(x0, y, x0 + w, y, fill=C["acc"], width=2)

    def toggle_rec(self):
        """RX-audio opnemen als WAV (max. 10 min) – handig om een probleem te laten zien of later te decoderen."""
        a = self.audio
        if a is None:
            self._set_status(self.t("eerst audio starten", "start the audio first"), C["amber"])
            return
        if a.rec is None:
            a.rec_n, a.rec_full = 0, False
            a.rec = []
            self.rec_btn.set_active(True, C["red"])
            self.rec_btn.config(text="■ REC")
            self._set_status(self.t("opnemen…  (klik nogmaals op REC om te stoppen)",
                                    "recording…  (click REC again to stop)"), C["red"])
            return
        blks, a.rec = a.rec, None
        self.rec_btn.set_active(False)
        self.rec_btn.config(text="● REC")
        if not blks:
            return
        x = np.concatenate(blks)
        d = os.path.join(DATA_DIR, "recordings")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, time.strftime("%Y-%m-%d_%H%M%SZ", time.gmtime()) +
                            f"_{self.freq / 1e6:.3f}MHz_{self._mode_text() or 'x'}.wav")
        write_wav(path, x, a.fs)
        self._set_status(self.t(f"opgenomen: {len(x) / a.fs:.0f} s – {path}", f"recorded: {len(x) / a.fs:.0f} s – {path}"),
                         C["green"])

    def decode_wav(self):
        """Een WAV-bestand decoderen (sneller dan realtime). Beelden komen in de galerij."""
        p = filedialog.askopenfilename(parent=self.root, initialdir=os.path.join(DATA_DIR, "recordings"),
                                       filetypes=[("WAV", "*.wav"), ("*", "*.*")])
        if not p:
            return
        try:
            x, fs = read_wav(p)
        except Exception as e:
            messagebox.showerror("WAV", str(e))
            return
        self._set_status(self.t(f"WAV decoderen: {os.path.basename(p)} ({len(x) / fs:.0f} s)…",
                                f"decoding WAV: {os.path.basename(p)} ({len(x) / fs:.0f} s)…"), C["acc2"])
        res = {"n": 0}

        def ev(*e):
            if e[0] == "done":
                res["n"] += 1
            self.gq.put(("rxev",) + e)

        def work():
            dec = sstv.SSTVDecoder(fs, ev)
            dec.set_forced(next((m for m in sstv.MODES if m.name == self.rxmode_var.get()), None))
            dec.slant = bool(self.slant_var.get())
            dec.set_filter(self.cfg.get("rx_filter", "normal"))
            dec.denoise = bool(self.cfg.get("denoise", True))
            blk = fs // 10
            for i in range(0, len(x), blk):
                dec.process(x[i:i + blk])
            dec.process(np.zeros(fs * 3, np.float32))
            dec.stop()
            self.gq.put(("wavdone", os.path.basename(p), res["n"]))
        threading.Thread(target=work, daemon=True).start()

    def stop_rx(self):
        if self.audio:
            self.audio.decoder.stop()

    def save_rx_as(self):
        if self.rx_img is None or Image is None:
            return
        p = filedialog.asksaveasfilename(parent=self.root, defaultextension=".png", initialfile="sstv.png",
                                         filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")])
        if p:
            Image.fromarray(self.rx_img).save(p)

    def rx_to_tx(self):
        if self.rx_img is not None and Image is not None:
            self.tx_src = Image.fromarray(self.rx_img)
            self.cfg["tx_image"] = ""
            self.compose()

    def open_rx_dir(self):
        os.makedirs(RX_DIR, exist_ok=True)
        try:
            os.startfile(RX_DIR)
        except Exception:
            webbrowser.open("file://" + RX_DIR)

    # ---------- galerij
    def _load_gallery(self):
        self.gallery = []
        try:
            files = [os.path.join(RX_DIR, f) for f in os.listdir(RX_DIR) if f.lower().endswith(".png")]
        except OSError:
            return
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for p in files[:60]:
            parts = os.path.basename(p)[:-4].split("_")
            self.gallery.append((p, parts[2] if len(parts) > 2 else "", os.path.getmtime(p)))

    def _thumb(self, path):
        if path in self._thumbs:
            return self._thumbs[path]
        ph = None
        try:
            if Image is not None:
                im = Image.open(path).convert("RGB")
                im.thumbnail((S(68), S(54)))
                ph = photo_from_array(np.asarray(im))
        except Exception:
            ph = None
        self._thumbs[path] = ph
        return ph

    def _fill_gallery(self):
        if not getattr(self, "gal", None):
            return
        self.gallery = self.gallery[:60]
        self.gal.delete(*self.gal.get_children())
        for i, (p, name, ts) in enumerate(self.gallery):
            ph = self._thumb(p)
            txt = "  " + time.strftime("%d-%m %H:%M", time.localtime(ts)) + "\n  " + name
            kw = {"image": ph} if ph else {}
            self.gal.insert("", "end", iid=str(i), text=txt, **kw)

    def _gal_select(self, ev=None):
        sel = self.gal.selection()
        if not sel or Image is None:
            return
        p, name, _ = self.gallery[int(sel[0])]
        try:
            self.rx_img = np.asarray(Image.open(p).convert("RGB"))
            self.rx_name = name or os.path.basename(p)
            self._rx_dirty = True
            self._draw_rx(force=True)
        except Exception as e:
            log_error("gallery", e)

    def _gal_open(self, ev=None):
        sel = self.gal.selection()
        if sel:
            p = self.gallery[int(sel[0])][0]
            try:
                os.startfile(p)
            except Exception:
                webbrowser.open("file://" + p)

    # ---------- TX-beeld
    def _tx_mode(self):
        return next((m for m in sstv.MODES if m.name == self.txmode_var.get()), sstv.MODES[0])

    def _tx_mode_changed(self):
        self.cfg["tx_mode"] = self._tx_mode().key
        self.compose()

    def _load_tx_src(self, path, quiet=False):
        if not path or Image is None:
            return False
        try:
            self.tx_src = load_image(path)
            self.cfg["tx_image"] = path
            return True
        except Exception as e:
            if not quiet:
                messagebox.showerror(self.t("Beeld", "Picture"), str(e))
            return False

    def choose_tx_image(self):
        if Image is None:
            messagebox.showerror("Pillow", "pip install pillow")
            return
        p = filedialog.askopenfilename(parent=self.root, filetypes=[
            (self.t("Beelden", "Pictures"), "*.jpg *.jpeg *.png *.bmp *.gif *.webp *.tif *.tiff"), ("*", "*.*")])
        if p and self._load_tx_src(p):
            self.compose()
            self._save_cfg()

    def clear_tx_image(self):
        self.tx_src = None
        self.cfg["tx_image"] = ""
        self.compose()

    def _expand(self, text):
        c = self.cfg
        return (text.replace("{MYCALL}", c.get("mycall", "").upper())
                    .replace("{MYNAME}", c.get("myname", ""))
                    .replace("{MYQTH}", c.get("myqth", ""))
                    .replace("{CALL}", self.call_var.get().upper())
                    .replace("{NAME}", self.hisname_var.get())
                    .replace("{RSV}", self.rst_var.get().upper())
                    .replace("{RST}", self.rst_var.get().upper()))

    def _compose_later(self):
        if getattr(self, "_cjob", None):
            self.root.after_cancel(self._cjob)
        self._cjob = self.root.after(350, self.compose)

    def compose(self):
        self._cjob = None
        m = self._tx_mode()
        if Image is None:
            self.tx_img = default_background(m.width, m.height)
        else:
            lines = self._expand(self.tx_var.get()).split("|")
            inset = self.last_rx if (self.inset_var.get() and self.last_rx is not None) else None
            try:
                self.tx_img = compose_tx(m, self.tx_src, lines, inset)
            except Exception as e:
                log_error("compose", e)
                self.tx_img = default_background(m.width, m.height)
        self.cfg["inset"] = bool(self.inset_var.get())
        self._draw_tx()

    def _draw_tx(self):
        c = getattr(self, "tx_canvas", None)
        if c is None or self.tx_img is None:
            return
        m = self._tx_mode()
        dur = sstv.tx_duration(m, self.cfg.get("preamble", True))
        self.tx_title.config(text=self.t(f"ZENDEN  ·  {m.name}  ·  {m.width}×{m.height}  ·  {dur:.0f} s",
                                         f"TRANSMIT  ·  {m.name}  ·  {m.width}×{m.height}  ·  {dur:.0f} s"))
        cw, ch = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        img = self.tx_img
        w, h = fit_size(img.shape[1], img.shape[0], cw - 8, ch - 8)
        self._tx_photo = photo_from_array(sstv.resize_nearest(img, w, h) if w > img.shape[1] else
                                          sstv.resize_area(img, w, h))
        c.delete("all")
        x0, y0 = (cw - w) // 2, (ch - h) // 2
        c.create_image(x0, y0, anchor="nw", image=self._tx_photo)
        if self.sender.busy:
            p = self.sender.progress
            y = y0 + h * p
            c.create_rectangle(x0, y, x0 + w, y0 + h, fill="#000000", stipple="gray50", outline="")
            c.create_line(x0, y, x0 + w, y, fill=C["red"], width=2)
            left = max(0, self.sender.total * (1 - p))
            self.txinfo_lbl.config(text=self.t(f"zenden… nog {left:.0f} s", f"transmitting… {left:.0f} s left"),
                                   fg=C["red"])
        else:
            src = os.path.basename(self.cfg.get("tx_image", "")) if self.tx_src is not None else ""
            self.txinfo_lbl.config(text=src[:34] or self.t("standaardachtergrond", "default background"),
                                   fg=C["dim"])

    def template(self, key):
        self.tx_var.set(self.cfg["templates"][key][1])
        self.compose()
        return "break"

    def _check_tx(self):
        t = self.t
        if not self.civ or self.detecting:
            self._set_status(t("eerst radio verbinden", "connect the radio first"), C["red"])
            return False
        if not self.cfg.get("mycall"):
            self._set_status(t("vul eerst je call in bij Instellingen", "enter your call in Settings first"), C["red"])
            return False
        if sd is None or not self.out_var.get():
            self._set_status(t("geen TX-audioapparaat gekozen", "no TX audio device selected"), C["red"])
            return False
        if self.sender.busy:
            return False
        if not self.data or self.mode not in ("USB", "LSB", "FM"):
            # USB-audio is alleen modulatiebron in de DATA-modus (USB-D / LSB-D / FM-D)
            if self.mode in ("USB", "LSB", "FM"):
                code = self.mode_code
            else:
                code = 0x01 if (self.freq or 14e6) >= 10e6 else 0x00
            self.set_mode(code, True)
            self.root.after(700, self._ensure_wide)
        return True

    def send_image(self):
        t = self.t
        if not self._check_tx():
            return
        self.compose()
        call = self.cfg.get("mycall", "").upper()
        if call and call not in self._expand(self.tx_var.get()).upper():
            if not messagebox.askyesno(t("Zenden", "Transmit"), t(
                    f"Je eigen call ({call}) staat niet in de tekst op het beeld.\nToch zenden?",
                    f"Your own call ({call}) is not in the text on the picture.\nTransmit anyway?"), parent=self.root):
                return
        m = self._tx_mode()
        try:
            dev = self._dev_index("output", self.out_var.get())
        except ValueError as e:
            self._set_status(str(e), C["red"])
            return
        self._save_cfg()
        lvl = min(max(self.lvl_var.get(), 1), 100) / 100 * 0.9
        # even wachten zodat de DATA-modus actief is voor de PTT
        self.root.after(250, lambda: self.sender.start(m, self.tx_img.copy(), dev, lvl,
                                                         self.cfg.get("preamble", True)))
        self._set_status(t(f"voorbereiden: {m.name}…", f"preparing: {m.name}…"), C["amber"])

    def stop_tx(self):
        self.sender.stop()
        self._set_tx(False)
        if self.audio:
            self.audio.decoder.muted = False

    # ---------- waterval & scope
    def _scope_row(self, v):
        s = self.scope
        key = (s["start"], s["end"], len(v))
        if key != getattr(self, "_sc_key", None):          # andere span/frequentie: opnieuw beginnen
            self._sc_key = key
            self._sc_hist = []
        self._sc_hist.append(np.asarray(v, dtype=np.float32))
        if len(self._sc_hist) > 120:
            del self._sc_hist[:len(self._sc_hist) - 120]
        floor = float(np.percentile(v, 25))
        sf = getattr(self, "_scope_floor", None)
        self._scope_floor = floor if sf is None else 0.85 * sf + 0.15 * floor
        rng = max(0.05, 0.42 * (1.15 - self.scope_gain.get()))
        self.wf_scope.add_row((v - self._scope_floor) / rng + 0.12)
        self.cfg["scope_gain"] = round(self.scope_gain.get(), 2)

    def _audio_row(self, db, fs):
        p = np.percentile(db, 75) - 10
        self.wf_floor = p if self.wf_floor is None else 0.9 * self.wf_floor + 0.1 * p
        self.wf_audio.add_row((db - self.wf_floor - self.ref_var.get()) / self.range_var.get())

    def _poll(self):
        if self.civ:
            try:
                self.poll_n += 1
                if self.detecting:
                    self.root.after(200, self._poll)
                    return
                if self.tx:
                    for sub in (b"\x11", b"\x12", b"\x13", b"\x16"):   # PO, SWR, ALC, Id
                        self.civ.send(0x15, sub)
                else:
                    self.civ.send(0x15, b"\x02")
                if self.poll_n % 10 == 0:
                    self.civ.send(0x15, b"\x15")                         # Vd
                if self.poll_n % 3 == 0:
                    self.civ.send(0x1C, b"\x00")
                if self.poll_n % 5 == 0:
                    self.civ.send(0x03)
                    self.civ.send(0x04)
                    self.civ.send(0x1A, b"\x06")
            except Exception:
                self._set_status(self.t("seriële fout", "serial error"), C["red"])
        self.root.after(200, self._poll)

    def _set_range(self, rg):
        self.cfg["audio_range"] = int(rg)
        for k, bt in self.range_btns.items():
            bt.set_active(k == rg)
        self.wf_audio.arr[:] = 0
        self.wf_audio.row = None
        self.wf_floor = None
        self._save_cfg()

    def _detect_sstv(self, now):
        """SSTV-signalen in de spectrumscope herkennen en met een kader + label markeren."""
        s = self.scope
        hist = getattr(self, "_sc_hist", [])
        span = s["end"] - s["start"]
        found = []
        if span > 0 and len(hist) >= 40 and self.civ:
            nb = len(hist[-1])
            hz = span / nb
            for b0, b1, score in sstv.detect_sstv(np.array(hist[-100:]), hz):
                found.append((s["start"] + b0 * hz, s["start"] + b1 * hz))
        boxes = [b for b in getattr(self, "sstv_boxes", []) if now - b["t"] < 4.0]
        for f0, f1 in found:
            c = (f0 + f1) / 2
            for b in boxes:
                if abs((b["f0"] + b["f1"]) / 2 - c) < 400:
                    b.update(f0=f0, f1=f1, t=now)
                    break
            else:
                boxes.append(dict(f0=f0, f1=f1, t=now))
        self.sstv_boxes = boxes
        out = []
        if span > 0:
            for b in boxes:
                if b["f1"] < s["start"] or b["f0"] > s["end"]:
                    continue
                dial = self._sstv_dial(b)
                lbl = "SSTV" + (f"  {dial / 1e6:.4f}" if dial else "")
                out.append(((b["f0"] - s["start"]) / span, (b["f1"] - s["start"]) / span, lbl, C["green"]))
        self.wf_scope.boxes = out

    def _sstv_dial(self, b):
        """Afstemfrequentie voor een herkend SSTV-signaal: het midden van 1200-2300 Hz is 1750 Hz audio.
        Boven 10 MHz USB (dial = midden - 1750), daaronder LSB (dial = midden + 1750)."""
        c = (b["f0"] + b["f1"]) / 2
        usb = self.mode == "USB" if self.mode in ("USB", "LSB") else (self.freq or c) >= 10e6
        return int(round((c - 1750 if usb else c + 1750) / 10) * 10)

    def _scope_label(self, frac):
        s = self.scope
        if s["end"] <= s["start"]:
            return ""
        return f"{(s['start'] + frac * (s['end'] - s['start'])) / 1000:.1f}"

    def _scope_click(self, frac):
        """Klik midden op een SSTV-signaal: de radio zo afstemmen dat dat punt op 1900 Hz audio valt
        (USB: dial = klik - 1900, LSB: dial = klik + 1900).  Bij AM/FM: direct op de klik."""
        s = self.scope
        if s["end"] > s["start"]:
            f = s["start"] + frac * (s["end"] - s["start"])
            for b in getattr(self, "sstv_boxes", []):          # klik in een SSTV-kader: precies erop
                if b["f0"] - 150 <= f <= b["f1"] + 150:
                    self.set_freq(self._sstv_dial(b))
                    return
            if self.mode == "USB":
                f -= 1750
            elif self.mode == "LSB":
                f += 1750
            self.set_freq(int(round(f / 10) * 10))

    # ---------- radio-bediening
    def set_freq(self, hz):
        if not self.civ:
            return
        self.civ.send(0x05, int_to_bcd_le(int(hz), 5))
        self.gq.put(("freq", int(hz)))

    def set_band(self, hz):
        """Band-knop: naar de SSTV-frequentie, LSB-D onder 10 MHz, USB-D daarboven."""
        if not self.civ:
            return
        self.set_freq(hz)
        self.set_mode(0x00 if hz < 10e6 else 0x01, True)
        self.root.after(700, self._ensure_wide)

    def _ensure_wide(self):
        """SSB-D staat op de IC-7300 standaard op een smal filter (FIL1 1,2 kHz); SSTV wil 2,4 kHz of meer."""
        if self.civ and self.mode in ("USB", "LSB") and self.ifw and self.ifw < 2400:
            self.set_ifw(2700)
            self._set_status(self.t(f"IF-filter was {self.ifw} Hz – op 2,7 kHz gezet voor SSTV",
                                    f"IF filter was {self.ifw} Hz – set to 2.7 kHz for SSTV"), C["amber"])

    def set_freq_khz(self):
        try:
            self.set_freq(int(round(float(self.fentry.get().replace(",", ".")) * 1000)))
        except ValueError:
            pass

    def set_mode(self, m, data=False):
        if self.civ:
            self.civ.send(0x06, bytes([m, self.filt]))
            if m in (0x00, 0x01, 0x02, 0x05):
                self.civ.send(0x1A, b"\x06" + (bytes([0x01, self.filt]) if data else b"\x00\x00"))
                self.data = data
            self.mode_code = m
            self.mode = MODES.get(m, self.mode)
            self._update_highlights()
            self.civ.send(0x04)
            self.civ.send(0x1A, b"\x03")
            self.root.after(400, lambda: self.civ and (self.civ.send(0x04), self.civ.send(0x1A, b"\x06")))

    def set_filter(self, i):
        if self.civ:
            self.filt = i
            self.civ.send(0x06, bytes([self.mode_code, i]))
            if self.data:
                self.civ.send(0x1A, b"\x06" + bytes([0x01, i]))
            self.civ.send(0x04)
            self.civ.send(0x1A, b"\x03")
            self._update_highlights()

    def set_ifw(self, w):
        if not self.civ:
            return
        if self.mode not in ("LSB", "USB"):
            self._set_status(self.t("IF-breedte alleen in SSB", "IF width only in SSB"), C["amber"])
            return
        idx = w // 50 - 1 if w <= 500 else 9 + (w - 500) // 100
        self.civ.send(0x1A, b"\x03" + int_to_bcd_be(int(idx), 1))
        self.civ.send(0x1A, b"\x03")

    def toggle_func(self, key):
        if not self.civ:
            return
        v = self.func_state.get(key, 0)
        if key == "pamp":
            v = (v + 1) % 3
            self.civ.send(0x16, bytes([0x02, v]))
            self.civ.send(0x16, b"\x02")
        elif key == "att":
            v = 0 if v else 1
            self.civ.send(0x11, bytes([0x20 if v else 0x00]))
            self.civ.send(0x11)
        elif key == "atu":
            self.civ.send(0x1C, bytes([0x01, 0 if v else 1]))
            self.civ.send(0x1C, b"\x01")
        else:
            sub = {"nb": 0x22, "nr": 0x40}[key]
            self.civ.send(0x16, bytes([sub, 0 if v else 1]))
            self.civ.send(0x16, bytes([sub]))

    def _send_level(self, sub, var):
        if self.civ:
            self.civ.send(0x14, bytes([sub]) + int_to_bcd_be(int(min(max(var.get(), 0), 255)), 2))

    def _send_af(self, final):
        self._send_level(0x01, self.af_var)

    def _send_rf(self, final):
        self._send_level(0x02, self.rf_var)

    def _send_pwr(self, final):
        self._send_level(0x0A, self.pwr_var)

    def atu_tune(self):
        if self.civ and not self.detecting:
            self.civ.send(0x1C, b"\x01\x02")
            self._set_status(self.t("tuner stemt af…", "tuner tuning…"), C["amber"])
            self.root.after(800, lambda: self.civ and self.civ.send(0x1C, b"\x01"))

    def open_manual(self):
        fn = "HANDLEIDING.html" if self.lang == "nl" else "MANUAL.html"
        for base in (os.path.join(APP_DIR, "docs"), os.path.join(os.path.dirname(APP_DIR), "docs")):
            p = os.path.join(base, fn)
            if os.path.exists(p):
                webbrowser.open("file:///" + p.replace(os.sep, "/"))
                return
        webbrowser.open(f"{REPO_URL}/blob/main/docs/{fn.replace('.html', '.md')}")

    def qrz_lookup(self):
        call = self.call_var.get().strip().upper()
        if call:
            webbrowser.open(f"https://www.qrz.com/db/{call}")

    # ---------- update-controle
    def check_update_async(self):
        if not self.cfg.get("check_updates", True):
            return

        def work():
            try:
                info = json.loads(http_get(RELEASE_API).decode("utf-8"))
                tag = info.get("tag_name", "")
                if tag and version_tuple(tag) > version_tuple(APP_VERSION):
                    assets = {a.get("name", ""): a.get("browser_download_url", "") for a in info.get("assets", [])}
                    upd = next((u for n, u in assets.items() if n.startswith("SSTVStation-Update") and n.endswith(".exe")), "")
                    full = next((u for n, u in assets.items() if n.startswith("SSTVStation-Setup") and n.endswith(".exe")), "")
                    self.gq.put(("update", {"version": tag.lstrip("vV"),
                                            "url": info.get("html_url") or f"{REPO_URL}/releases/latest",
                                            "asset": upd or full, "small": bool(upd)}))
            except Exception as e:
                log_error("update-check", e)
        threading.Thread(target=work, daemon=True).start()

    def _show_update_button(self):
        if self.update_info and self.upd_btn.winfo_exists():
            self.upd_btn.config(text="⬆ " + self.t(f"Nieuwe versie {self.update_info['version']}",
                                                  f"New version {self.update_info['version']}"))
            self.upd_btn.set_active(True, C["acc2"])
            self.upd_btn.pack(side="left", padx=(8, 0))

    def _ask_update(self):
        v = self.update_info["version"]
        if self.cfg.get("update_asked") == v:
            return
        self.cfg["update_asked"] = v
        self._save_cfg()
        self._open_update()

    def _open_update(self):
        """Knop / melding: nu bijwerken (downloaden + installeren + herstarten) of de releasepagina openen."""
        info = self.update_info
        if not info:
            return
        v = info["version"]
        if not info.get("asset") or not sys.platform.startswith("win"):
            if messagebox.askyesno(self.t("Update beschikbaar", "Update available"), self.t(
                    f"Er is een nieuwe versie van SSTV Station: {v} (jij hebt {APP_VERSION}).\n\n"
                    "Wil je de downloadpagina openen?",
                    f"A new version of SSTV Station is available: {v} (you have {APP_VERSION}).\n\n"
                    "Open the download page?"), parent=self.root):
                webbrowser.open(info["url"])
            return
        ans = messagebox.askyesnocancel(self.t("Update beschikbaar", "Update available"), self.t(
            f"Er is een nieuwe versie van SSTV Station: {v} (jij hebt {APP_VERSION}).\n\n"
            "Nu bijwerken? Het programma downloadt de update, sluit af, installeert en start opnieuw. "
            "Instellingen, logboek en ontvangen beelden blijven bewaard.\n\n"
            "Ja = nu bijwerken   ·   Nee = wat is er nieuw (releasepagina)",
            f"A new version of SSTV Station is available: {v} (you have {APP_VERSION}).\n\n"
            "Update now? The program downloads the update, closes, installs and restarts. "
            "Settings, logbook and received pictures are kept.\n\n"
            "Yes = update now   ·   No = what's new (release page)"), parent=self.root)
        if ans is None:
            return
        if ans is False:
            webbrowser.open(info["url"])
            return
        if self.sender.busy:
            self._set_status(self.t("eerst het zenden afronden", "finish transmitting first"), C["red"])
            return
        if self.audio and self.audio.decoder.state == "rx":
            if not messagebox.askyesno(self.t("Update", "Update"), self.t(
                    "Er wordt nu een beeld ontvangen. Toch bijwerken? (het deelbeeld wordt bewaard)",
                    "A picture is being received. Update anyway? (the partial picture is saved)"), parent=self.root):
                return
        self._set_status(self.t(f"update {v} downloaden…", f"downloading update {v}…"), C["acc2"])

        def work():
            try:
                data = http_get(info["asset"], timeout=120, accept="application/octet-stream")
                if len(data) < 100_000 or data[:2] != b"MZ":
                    raise OSError(f"unexpected download ({len(data)} bytes)")
                path = os.path.join(os.environ.get("TEMP") or DATA_DIR, os.path.basename(info["asset"].split("?")[0]))
                with open(path, "wb") as f:
                    f.write(data)
                self.gq.put(("upd_ready", path, info.get("small", False)))
            except Exception as e:
                log_error("update-download", e)
                self.gq.put(("upd_fail", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _run_update(self, path, small):
        """Update starten en dit programma afsluiten. De kleine update installeert stil en start daarna opnieuw."""
        import subprocess
        try:
            if self.audio:
                self.audio.decoder.stop()
            args = [path, "/S"] if small else [path]
            subprocess.Popen(args, close_fds=True)
        except Exception as e:
            log_error("update-run", e)
            messagebox.showerror("Update", str(e))
            return
        self._quit()

    # ---------- audio-diagnose
    def _audio_check(self, now):
        a = self.audio
        if not a:
            self._silent_since = None
            return
        if a.error and not getattr(self, "_err_shown", False):
            self._err_shown = True
            self._set_status(self.t(f"audiofout: {a.error} (zie error.log)", f"audio error: {a.error} (see error.log)"),
                             C["red"])
        if a.blocks == 0 or a.level_db < -100:
            if self._silent_since is None:
                self._silent_since = now
            elif now - self._silent_since > 3 and not getattr(self, "_silence_shown", False):
                self._silence_shown = True
                self._set_status(self.t(
                    "Audio: geen geluid ontvangen – klik hier voor Windows-microfoontoegang",
                    "Audio: no sound received – click here for Windows microphone access"), C["red"])
                self._status_action = "mic"
        else:
            self._silent_since = None
            if getattr(self, "_silence_shown", False):
                self._silence_shown = False
                self._status_action = None
                self._set_status(self.t("Audio ontvangen", "Audio received"), C["green"])

    def _status_click(self, ev=None):
        if getattr(self, "_status_action", None) == "mic":
            webbrowser.open("ms-settings:privacy-microphone")

    # ---------- SSTV-hulp (linksonder)
    def _tool_tab(self, key):
        self.tool_tab = key
        for k, bt in self.tool_btns.items():
            bt.set_active(k == key)
        for w in self.tool_body.winfo_children():
            w.destroy()
        b = self.tool_body
        t = self.t
        if key == "dec":
            top = tk.Frame(b, bg=C["panel"])
            top.pack(fill="x")
            self.td_state = tk.Label(top, text="--", bg=C["panel"], fg=C["acc"], font=(DIGIT, S(17), "bold"),
                                     anchor="w")
            self.td_state.pack(side="left", fill="x", expand=True)
            fr = tk.Frame(b, bg=C["panel"])
            fr.pack(fill="x", pady=(2, 2))
            label(fr, t("Filter", "Filter"), font=(UI, S(8))).pack(side="left", padx=(0, 4))
            self.filt_btns = {}
            for k_, nl_, en_ in (("wide", "Scherp", "Sharp"), ("normal", "Normaal", "Normal"),
                                 ("narrow", "Ruisarm", "Low noise")):
                bt = FlatButton(fr, t(nl_, en_), command=lambda k_=k_: self._set_rx_filter(k_), padx=6, pady=1,
                                font=(UI, S(8), "bold"))
                bt.pack(side="left", padx=1)
                bt.set_active(self.cfg.get("rx_filter", "normal") == k_)
                self.filt_btns[k_] = bt
            self.dn_btn = FlatButton(fr, t("Ruisfilter", "Denoise"), command=self._toggle_denoise, padx=6, pady=1,
                                     font=(UI, S(8), "bold"))
            self.dn_btn.pack(side="right")
            self.dn_btn.set_active(bool(self.cfg.get("denoise", True)))
            grid = tk.Frame(b, bg=C["panel"])
            grid.pack(fill="x", pady=(2, 4))
            self.td = {}
            for i, (k, nl, en) in enumerate((("line", "Regel", "Line"), ("vis", "VIS", "VIS"),
                                             ("off", "Afstemfout", "Tuning offset"),
                                             ("slant", "Klokfout (slant)", "Clock error (slant)"),
                                             ("sync", "Syncpulsen", "Sync pulses"),
                                             ("last", "Laatste VIS", "Last VIS"),
                                             ("tone", "Gemeten toon", "Measured tone"),
                                             ("prate", "Syncpulsen per s", "Sync pulses per s"))):
                label(grid, t(nl, en), font=(UI, S(8))).grid(row=i, column=0, sticky="w")
                v = label(grid, "--", fg=C["text"], font=(UI, S(9), "bold"))
                v.grid(row=i, column=1, sticky="e", padx=(10, 0))
                self.td[k] = v
            grid.columnconfigure(1, weight=1)
            self.td_hint = tk.Label(b, text="", bg=C["panel"], fg=C["amber"], font=(UI, S(8), "bold"),
                                    justify="left", anchor="w", wraplength=S(330))
            self.td_hint.pack(fill="x")
        elif key == "modes":
            tree = ttk.Treeview(b, columns=("vis", "size", "sec"), show="tree headings", height=7)
            tree.heading("#0", text=t("Modus", "Mode"), anchor="w")
            tree.column("#0", width=S(110))
            for c_, w_, h_ in (("vis", S(40), "VIS"), ("size", S(70), t("Formaat", "Size")), ("sec", S(46), "s")):
                tree.heading(c_, text=h_, anchor="w")
                tree.column(c_, width=w_, anchor="w")
            for m in sstv.MODES:
                tree.insert("", "end", text=m.name, values=(m.vis, f"{m.width}×{m.height}", f"{m.seconds:.0f}"))
            tree.pack(fill="both", expand=True)
            tree.bind("<Double-Button-1>", lambda e: self._pick_mode(tree))
            label(b, t("Dubbelklik: als TX-modus", "Double-click: use as TX mode"), font=(UI, S(8))).pack(anchor="w")
        else:
            tree = ttk.Treeview(b, columns=("mhz", "sb", "note"), show="headings", height=7)
            for c_, w_, h_ in (("mhz", S(70), "MHz"), ("sb", S(40), "SB"), ("note", S(120), t("Opmerking", "Note"))):
                tree.heading(c_, text=h_, anchor="w")
                tree.column(c_, width=w_, anchor="w")
            for i, (band, hz, sb, note) in enumerate(SSTV_FREQS):
                tree.insert("", "end", iid=str(i), values=(f"{hz / 1e6:.3f}", sb, f"{band}  {note}"))
            tree.pack(fill="both", expand=True)
            tree.bind("<Double-Button-1>", lambda e: self._pick_freq(tree))
            label(b, t("Dubbelklik: afstemmen (met DATA-modus)", "Double-click: tune (with DATA mode)"),
                  font=(UI, S(8))).pack(anchor="w")
        b.update_idletasks()

    def _pick_mode(self, tree):
        sel = tree.selection()
        if sel:
            self.txmode_var.set(tree.item(sel[0], "text"))
            self._tx_mode_changed()

    def _pick_freq(self, tree):
        sel = tree.selection()
        if sel:
            self.set_band(SSTV_FREQS[int(sel[0])][1])

    def _tool_refresh(self, now):
        if self.tool_tab != "dec" or now - getattr(self, "_td_t", 0) < 0.3:
            return
        self._td_t = now
        if not getattr(self, "td_state", None) or not self.td_state.winfo_exists():
            return
        t = self.t
        if not self.audio:
            self.td_state.config(text=t("audio uit", "audio off"), fg=C["dim"])
            for v in self.td.values():
                v.config(text="--")
            return
        st = self.audio.decoder.status()
        if self.tx:
            self.td_state.config(text=t("zenden", "transmitting"), fg=C["red"])
        elif st["state"] == "rx":
            self.td_state.config(text=st["mode"], fg=C["acc"])
        else:
            self.td_state.config(text=t("wacht op VIS…", "waiting for VIS…"), fg=C["dim"])
        rx = st["state"] == "rx"
        self.td["line"].config(text=f"{st['line']} / {st['lines']}" if rx else "--")
        self.td["vis"].config(text=(t("ja", "yes") if st["vis"] else t("nee (vrijloop)", "no (free-run)")) if rx else "--")
        self.td["off"].config(text=signed(st["offset"], "Hz") if rx and st["vis"] else "--")
        self.td["slant"].config(text=signed(st["slant"], "ppm") if rx else "--")
        self.td["sync"].config(text=f"{min(st['sync'], 1) * 100:.0f}%" if rx else "--")
        self.td["last"].config(text=st["last_vis"] or "--")
        tone = st["tone"]
        self.td["tone"].config(text=f"{tone:.0f} Hz" if self.audio.level_db > -90 else "--")
        self.td["prate"].config(text=f"{st['pulses_s']:.1f}")
        self.td_hint.config(text=self._rx_hint(st))

    def _rx_hint(self, st):
        """Korte uitleg waarom er (misschien) niets gedecodeerd wordt."""
        t = self.t
        if self.tx or st["state"] == "rx":
            return ""
        if self.audio.level_db < -90:
            return t("⚠ Geen audio. Kies bij RX de Microfoon (USB Audio CODEC).",
                     "⚠ No audio. Select Microphone (USB Audio CODEC) for RX.")
        if "CODEC" not in self.dev_var.get().upper():
            return t("⚠ RX staat niet op de USB Audio CODEC van de radio.",
                     "⚠ RX is not set to the radio's USB Audio CODEC.")
        if self.freq and self.mode in ("USB", "LSB"):
            want = "USB" if self.freq >= 10e6 else "LSB"
            if self.mode != want:
                return t(f"⚠ Op deze band zendt SSTV in {want}; de radio staat op {self.mode}.",
                         f"⚠ SSTV on this band uses {want}; the radio is in {self.mode}.")
            if self.ifw and self.ifw < 2400:
                return t(f"⚠ IF-filter {self.ifw} Hz is te smal; kies 2.4k of breder.",
                         f"⚠ IF filter {self.ifw} Hz is too narrow; choose 2.4k or wider.")
        return t("Wacht op een beeld. Een SSTV-signaal ligt in de audiowaterval tussen de rode lijn (1200 Hz) "
                 "en 2300 Hz.", "Waiting for a picture. An SSTV signal lies in the audio waterfall between "
                 "the red line (1200 Hz) and 2300 Hz.")

    # ---------- enquête
    def open_survey(self):
        self.cfg["survey_asked"] = True
        webbrowser.open(f"{SURVEY_URL}?lang={self.lang}&v={APP_VERSION}")

    def maybe_ask_survey(self):
        self.cfg["starts"] = int(self.cfg.get("starts", 0)) + 1
        self._save_cfg()
        if self.cfg["starts"] >= 5 and not self.cfg.get("survey_asked"):
            self.cfg["survey_asked"] = True
            self._save_cfg()
            if messagebox.askyesno(self.t("Enquête", "Survey"), self.t(
                    "Je gebruikt SSTV Station nu een tijdje. Wil je een korte enquête invullen (ca. 2 minuten)?\n\n"
                    "Je bevindingen helpen om het programma te verbeteren. "
                    "Later kan het ook via de knop 'Enquête'.",
                    "You have been using SSTV Station for a while. Would you like to fill in a short survey "
                    "(about 2 minutes)?\n\nYour findings help to improve the program. "
                    "You can also use the 'Survey' button later."), parent=self.root):
                self.open_survey()

    # ---------- logboek
    def log_qso(self):
        call = self.call_var.get().strip().upper()
        if not call:
            self._set_status(self.t("geen call ingevuld", "no call entered"), C["amber"])
            return "break"
        band = ""
        for k, (lo, hi) in BAND_RANGES.items():
            if lo <= self.freq <= hi:
                band = f"{k}m"
        now = time.gmtime()
        rec = {"CALL": call, "QSO_DATE": time.strftime("%Y%m%d", now), "TIME_ON": time.strftime("%H%M%S", now),
               "BAND": band, "FREQ": f"{self.freq / 1e6:.6f}" if self.freq else "", "MODE": "SSTV",
               "RST_SENT": self.rst_var.get(), "RST_RCVD": self.rstr_var.get(),
               "NAME": self.hisname_var.get().strip(), "COMMENT": self.rx_name or self._tx_mode().name,
               "STATION_CALLSIGN": self.cfg.get("mycall", "").upper()}
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            append_qso(LOG_FILE, rec)
        except OSError as e:
            messagebox.showerror(self.t("Logboek", "Logbook"), str(e))
            return "break"
        self.call_var.set("")
        self.hisname_var.set("")
        self.rstr_var.set("595")
        self._set_status(self.t(f"QSO met {call} gelogd", f"QSO with {call} logged"), C["green"])
        self.compose()
        return "break"

    def show_log(self):
        t = self.t
        w = tk.Toplevel(self.root)
        w.title(t("Logboek", "Logbook"))
        w.configure(bg=C["bg"])
        w.geometry("960x520")
        f, b, head = panel(w, t("LOGBOEK", "LOGBOOK") + f"  ·  {LOG_FILE}")
        f.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("date", "time", "call", "band", "freq", "rsts", "rstr", "name", "cmt")
        tree = ttk.Treeview(b, columns=cols, show="headings")
        for c_, w_, h_ in (("date", 90, t("Datum", "Date")), ("time", 60, "UTC"), ("call", 100, "Call"),
                           ("band", 55, "Band"), ("freq", 95, "MHz"), ("rsts", 60, "RSV snt"),
                           ("rstr", 60, "RSV rcv"), ("name", 130, t("Naam", "Name")), ("cmt", 120, t("Modus", "Mode"))):
            tree.heading(c_, text=h_, anchor="w")
            tree.column(c_, width=w_, anchor="w")
        sb = ttk.Scrollbar(b, orient="vertical", command=tree.yview)
        tree.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        recs = read_adif(LOG_FILE)
        for q in reversed(recs):
            d = q.get("QSO_DATE", "")
            tm = q.get("TIME_ON", "")
            tree.insert("", "end", values=(f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d,
                                           f"{tm[:2]}:{tm[2:4]}" if len(tm) >= 4 else tm, q.get("CALL", ""),
                                           q.get("BAND", ""), q.get("FREQ", ""), q.get("RST_SENT", ""),
                                           q.get("RST_RCVD", ""), q.get("NAME", ""), q.get("COMMENT", "")))
        bar = tk.Frame(w, bg=C["bg"])
        bar.pack(fill="x", padx=10, pady=(0, 10))
        label(bar, t(f"{len(recs)} QSO's", f"{len(recs)} QSOs"), font=(UI, S(10))).pack(side="left")

        def export():
            p = filedialog.asksaveasfilename(parent=w, defaultextension=".adi", initialfile="sstvstation_log.adi",
                                             filetypes=[("ADIF", "*.adi *.adif")])
            if p:
                import shutil
                shutil.copyfile(LOG_FILE, p)

        def open_dir():
            try:
                os.startfile(DATA_DIR)
            except Exception:
                webbrowser.open("file://" + DATA_DIR)
        FlatButton(bar, t("Sluiten", "Close"), command=w.destroy).pack(side="right")
        if recs:
            FlatButton(bar, t("Exporteer ADIF…", "Export ADIF…"), command=export).pack(side="right", padx=6)
        FlatButton(bar, t("Open map", "Open folder"), command=open_dir).pack(side="right")

    # ---------- instellingen
    def show_settings(self):
        t = self.t
        w = tk.Toplevel(self.root)
        w.title(t("Instellingen", "Settings"))
        w.configure(bg=C["bg"])
        w.transient(self.root)
        f, b, _ = panel(w, t("ALGEMEEN", "GENERAL"))
        f.pack(fill="x", padx=12, pady=(12, 6))
        lang_v = tk.StringVar(value="Nederlands" if self.lang == "nl" else "English")
        tname = (lambda k: THEME_NL[k]) if self.lang == "nl" else (lambda k: k)
        theme_v = tk.StringVar(value=tname(self.cfg.get("theme", "Amber")))
        call_v = tk.StringVar(value=self.cfg.get("mycall", ""))
        name_v = tk.StringVar(value=self.cfg.get("myname", ""))
        qth_v = tk.StringVar(value=self.cfg.get("myqth", ""))
        pre_v = tk.BooleanVar(value=bool(self.cfg.get("preamble", True)))
        fr_v = tk.BooleanVar(value=bool(self.cfg.get("freerun", True)))
        auto_v = tk.BooleanVar(value=bool(self.cfg.get("autostart", True)))
        theme_cb = ttk.Combobox(b, textvariable=theme_v, state="readonly", width=14,
                                values=[tname(k) for k in THEMES])

        def live_theme(ev=None):
            th = next((k for k in THEMES if tname(k) == theme_v.get()), "Amber")
            if th != self.cfg.get("theme"):
                self.cfg["theme"] = th
                self._save_cfg()
                self.rebuild()
                w.lift()
        theme_cb.bind("<<ComboboxSelected>>", live_theme)
        small = (UI, S(9), "bold")
        grid = [(t("Taal", "Language"), ttk.Combobox(b, textvariable=lang_v, state="readonly", width=14,
                                                    values=["Nederlands", "English"])),
                (t("Kleur", "Colour"), theme_cb),
                (t("Mijn call", "My call"), entry(b, call_v, 14)),
                (t("Mijn naam", "My name"), entry(b, name_v, 14)),
                (t("Mijn QTH", "My QTH"), entry(b, qth_v, 14)),
                (t("Updates", "Updates"), FlatButton(b, t("Automatisch controleren", "Check automatically"),
                                                     var=self.upd_var, padx=8, pady=2, font=small)),
                (t("Zenden", "Transmit"), FlatButton(b, t("Kalibratietonen vóór VIS", "Calibration tones before VIS"),
                                                     var=pre_v, padx=8, pady=2, font=small)),
                (t("Ontvangen", "Receive"), FlatButton(b, t("Ook starten zonder VIS", "Also start without VIS"),
                                                       var=fr_v, padx=8, pady=2, font=small)),
                (t("Opstarten", "Start-up"), FlatButton(b, t("Radio en audio automatisch starten",
                                                             "Connect radio and start audio automatically"),
                                                        var=auto_v, padx=8, pady=2, font=small))]
        for i, (txt_, wd) in enumerate(grid):
            label(b, txt_, font=(UI, S(10))).grid(row=i // 2, column=(i % 2) * 2, sticky="w", padx=(0, 8), pady=4)
            wd.grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(0, 24), pady=4)

        f, b, _ = panel(w, t("BEELDTEKST-SJABLONEN  ·  | = nieuwe regel (1e regel boven, rest onder)  ·  "
                             "{MYCALL} {CALL} {RSV} {NAME} {MYNAME} {MYQTH}",
                             "PICTURE TEXT TEMPLATES  ·  | = new line (1st line on top, others at the bottom)  ·  "
                             "{MYCALL} {CALL} {RSV} {NAME} {MYNAME} {MYQTH}"))
        f.pack(fill="both", expand=True, padx=12, pady=6)
        mvars = {}
        for i, key in enumerate(sorted(self.cfg["templates"], key=lambda k: int(k[1:]))):
            lbl_, txt_ = self.cfg["templates"][key]
            lv, tv = tk.StringVar(value=lbl_), tk.StringVar(value=txt_)
            mvars[key] = (lv, tv)
            label(b, key, fg=C["acc"], font=(UI, S(10), "bold")).grid(row=i, column=0, sticky="w", pady=3)
            entry(b, lv, 9).grid(row=i, column=1, sticky="w", padx=6, pady=3)
            entry(b, tv, 70, font=(MONO, S(10))).grid(row=i, column=2, sticky="we", pady=3)
        b.columnconfigure(2, weight=1)

        bar = tk.Frame(w, bg=C["bg"])
        bar.pack(fill="x", padx=12, pady=(6, 12))

        def save():
            self.lang = "nl" if lang_v.get() == "Nederlands" else "en"
            theme = next((k for k in THEMES if tname(k) == theme_v.get()), "Amber")
            self.cfg["check_updates"] = bool(self.upd_var.get())
            self.cfg.update(theme=theme, mycall=call_v.get().strip().upper(), myname=name_v.get().strip(),
                            myqth=qth_v.get().strip(), lang=self.lang, preamble=bool(pre_v.get()),
                            freerun=bool(fr_v.get()), autostart=bool(auto_v.get()))
            for k, (lv, tv) in mvars.items():
                self.cfg["templates"][k] = [lv.get().strip() or k, tv.get()]
            self._save_cfg()
            self._apply_rx_opts()
            w.destroy()
            self.rebuild()
        FlatButton(bar, t("Opslaan", "Save"), command=save, accent=C["acc"]).pack(side="right")
        FlatButton(bar, t("Annuleren", "Cancel"), command=w.destroy).pack(side="right", padx=6)
        label(bar, f"{APP_NAME} {APP_VERSION}  ·  MIT License  ·  " +
              t("geleverd zoals het is, zonder garantie (zie handleiding)  ·  niet gelieerd aan Icom Inc.",
                "provided as is, without warranty (see manual)  ·  not affiliated with Icom Inc."),
              font=(UI, S(8)), bg=C["bg"]).pack(side="left")
        w.grab_set()

    def _quit(self):
        self._quitting = True
        self.sender.stop()
        self._save_cfg()
        if self.audio:
            self.audio.close()
        self._disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    root.geometry("1560x1000")
    try:
        root.state("zoomed")
    except tk.TclError:
        pass
    root.minsize(1000, 680)
    app = App(root)
    if not app.cfg.get("mycall"):
        root.after(600, app.show_settings)
    else:
        root.after(4000, app.maybe_ask_survey)
    root.after(800, app.autostart)
    root.after(3000, app.check_update_async)
    root.mainloop()


if __name__ == "__main__":
    main()
