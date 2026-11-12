# -*- coding: utf-8 -*-
"""
SSTV-kern voor SSTV Station: modi, VIS-code, encoder en (streaming) decoder.
SSTV core for SSTV Station: modes, VIS code, encoder and (streaming) decoder.

Copyright (c) 2026 Richard A. Hendriksen (PD3RAH)
Licensed under the MIT License - see LICENSE.

Alles in numpy; geen andere afhankelijkheden.  Frequenties: sync 1200 Hz, zwart 1500 Hz, wit 2300 Hz.
"""

import math
import threading
from collections import deque

import numpy as np

F_SYNC, F_BLACK, F_WHITE, F_LEADER = 1200.0, 1500.0, 2300.0, 1900.0
F_CENTER = 1750.0                                  # midden van de SSTV-band (1100-2400 Hz)
F_RANGE = F_WHITE - F_BLACK


# ----------------------------------------------------------------- modi
class Mode:
    """
    Eén SSTV-modus.  'segs' beschrijft één lijngroep (1 beeldlijn, of 2 bij Robot 36 en PD):
    ('t', ms, freq) = vaste toon,  ('s', ms, kanaal) = scan van 'width' pixels.
    Kanalen: R G B (RGB-modi) of Y Y0 Y1 U V (YUV-modi; U = B-Y, V = R-Y).
    """

    def __init__(self, key, name, vis, width, height, rows, segs, color="rgb", pre=()):
        self.key, self.name, self.vis = key, name, vis
        self.width, self.height, self.rows = width, height, rows
        self.segs, self.color, self.pre = segs, color, list(pre)
        self.groups = height // rows
        self.group_ms = sum(s[1] for s in segs)
        self.pre_ms = sum(s[1] for s in self.pre)
        # syncpulsen (1200 Hz, >= 4 ms) binnen een groep: (begin-ms, lengte-ms)
        self.syncs, t = [], 0.0
        for kind, ms, arg in segs:
            if kind == "t" and arg == F_SYNC and ms >= 4:
                self.syncs.append((t, ms))
            t += ms
        self.sync_len = self.syncs[0][1]
        self.scans, t = [], 0.0
        for kind, ms, arg in segs:
            if kind == "s":
                self.scans.append((t, ms, arg))
            t += ms

    @property
    def seconds(self):
        return (self.pre_ms + self.groups * self.group_ms) / 1000.0

    @property
    def sync_period_ms(self):
        """Afstand tussen syncpulsen als die gelijk verdeeld zijn, anders de groepsduur."""
        n = len(self.syncs)
        return self.group_ms / n


def _martin(key, name, vis, scan):
    t = ("t", 0.572, F_BLACK)
    return Mode(key, name, vis, 320, 256, 1,
                [("t", 4.862, F_SYNC), t, ("s", scan, "G"), t, ("s", scan, "B"), t, ("s", scan, "R"), t])


def _scottie(key, name, vis, scan):
    t = ("t", 1.5, F_BLACK)
    return Mode(key, name, vis, 320, 256, 1,
                [t, ("s", scan, "G"), t, ("s", scan, "B"), ("t", 9.0, F_SYNC), t, ("s", scan, "R")],
                pre=[("t", 9.0, F_SYNC)])


def _pd(key, name, vis, w, h, pix_ms):
    sc = round(pix_ms * w, 4)
    return Mode(key, name, vis, w, h, 2,
                [("t", 20.0, F_SYNC), ("t", 2.08, F_BLACK), ("s", sc, "Y0"), ("s", sc, "V"),
                 ("s", sc, "U"), ("s", sc, "Y1")], color="yuv")


MODES = [
    _martin("M1", "Martin M1", 44, 146.432),
    _martin("M2", "Martin M2", 40, 73.216),
    _scottie("S1", "Scottie S1", 60, 138.240),
    _scottie("S2", "Scottie S2", 56, 88.064),
    _scottie("SDX", "Scottie DX", 76, 345.6),
    Mode("R36", "Robot 36", 8, 320, 240, 2,
         [("t", 9.0, F_SYNC), ("t", 3.0, F_BLACK), ("s", 88.0, "Y0"), ("t", 4.5, F_BLACK), ("t", 1.5, F_LEADER),
          ("s", 44.0, "V"),
          ("t", 9.0, F_SYNC), ("t", 3.0, F_BLACK), ("s", 88.0, "Y1"), ("t", 4.5, F_WHITE), ("t", 1.5, F_LEADER),
          ("s", 44.0, "U")], color="yuv"),
    Mode("R72", "Robot 72", 12, 320, 240, 1,
         [("t", 9.0, F_SYNC), ("t", 3.0, F_BLACK), ("s", 138.0, "Y"), ("t", 4.5, F_BLACK), ("t", 1.5, F_LEADER),
          ("s", 69.0, "V"), ("t", 4.5, F_WHITE), ("t", 1.5, F_BLACK), ("s", 69.0, "U")], color="yuv"),
    _pd("PD50", "PD 50", 93, 320, 256, 0.286),
    _pd("PD90", "PD 90", 99, 320, 256, 0.532),
    _pd("PD120", "PD 120", 95, 640, 496, 0.190),
    _pd("PD160", "PD 160", 98, 512, 400, 0.382),
    _pd("PD180", "PD 180", 96, 640, 496, 0.286),
    _pd("PD240", "PD 240", 97, 640, 496, 0.382),
    _pd("PD290", "PD 290", 94, 800, 616, 0.286),
    Mode("SC2180", "Wraase SC2-180", 55, 320, 256, 1,
         [("t", 5.5225, F_SYNC), ("t", 0.5, F_BLACK), ("s", 235.0, "R"), ("s", 235.0, "G"), ("s", 235.0, "B")]),
]
MODE_BY_KEY = {m.key: m for m in MODES}
MODE_BY_VIS = {m.vis: m for m in MODES}


# ----------------------------------------------------------------- kleurconversie (ITU-R BT.601, zoals MMSSTV)
def rgb_to_yuv(rgb):
    r, g, b = (rgb[..., i].astype(np.float32) for i in range(3))
    y = 16.0 + (65.738 * r + 129.057 * g + 25.064 * b) / 256.0
    u = 128.0 + (-37.945 * r - 74.494 * g + 112.439 * b) / 256.0     # B-Y
    v = 128.0 + (112.439 * r - 94.154 * g - 18.285 * b) / 256.0      # R-Y
    return y, u, v


def yuv_to_rgb(y, u, v):
    y = 1.164 * (np.asarray(y, np.float32) - 16.0)
    u = np.asarray(u, np.float32) - 128.0
    v = np.asarray(v, np.float32) - 128.0
    rgb = np.stack([y + 1.596 * v, y - 0.813 * v - 0.392 * u, y + 2.017 * u], axis=-1)
    return np.clip(rgb + 0.5, 0, 255).astype(np.uint8)


# ----------------------------------------------------------------- encoder
def vis_tones(code):
    """VIS: leader 300 ms, break 10 ms, leader 300 ms, startbit, 7 databits (LSB eerst), even pariteit, stopbit."""
    out = [(300.0, F_LEADER), (10.0, F_SYNC), (300.0, F_LEADER), (30.0, F_SYNC)]
    ones = 0
    for i in range(7):
        bit = (code >> i) & 1
        ones += bit
        out.append((30.0, 1100.0 if bit else 1300.0))
    out.append((30.0, 1100.0 if ones % 2 else 1300.0))
    out.append((30.0, F_SYNC))
    return out


PREAMBLE = [(100.0, f) for f in (1900, 1500, 1900, 1500, 2300, 1500, 2300, 1500)]


def mode_channels(mode, rgb):
    """Beeld (H x W x 3, uint8, al op modusformaat) -> per groep een dict kanaal -> pixelwaarden 0..255."""
    rgb = np.asarray(rgb)
    out = []
    if mode.color == "rgb":
        for g in range(mode.groups):
            row = rgb[g].astype(np.float32)
            out.append({"R": row[:, 0], "G": row[:, 1], "B": row[:, 2]})
        return out
    y, u, v = rgb_to_yuv(rgb)
    for g in range(mode.groups):
        if mode.rows == 2:
            a, b = 2 * g, 2 * g + 1
            out.append({"Y0": y[a], "Y1": y[b], "U": (u[a] + u[b]) / 2, "V": (v[a] + v[b]) / 2})
        else:
            out.append({"Y": y[g], "U": u[g], "V": v[g]})
    return out


def tx_duration(mode, preamble=True):
    return mode.seconds + sum(d for d, _ in vis_tones(mode.vis)) / 1000 + \
        (sum(d for d, _ in PREAMBLE) / 1000 if preamble else 0)


def encode(mode, rgb, fs=48000, amp=0.7, preamble=True, progress=None):
    """
    Genereer het SSTV-audiosignaal (float32, mono) met continue fase.
    rgb moet al mode.height x mode.width x 3 zijn.  progress(regel) wordt per groep aangeroepen.
    """
    rgb = np.asarray(rgb)
    if rgb.shape[:2] != (mode.height, mode.width):
        raise ValueError(f"image must be {mode.width}x{mode.height}")
    total_ms = tx_duration(mode, preamble) * 1000 + 20
    n = int(math.ceil(total_ms * fs / 1000))
    freq = np.full(n, F_BLACK, dtype=np.float64)
    t = 0.0

    def tone(ms, f):
        nonlocal t
        a, b = int(round(t * fs / 1000)), int(round((t + ms) * fs / 1000))
        freq[a:b] = f
        t += ms

    def scan(ms, vals):
        nonlocal t
        a, b = int(round(t * fs / 1000)), int(round((t + ms) * fs / 1000))
        w = len(vals)
        idx = ((np.arange(a, b) / fs * 1000 - t) / ms * w).astype(np.int64)
        freq[a:b] = F_BLACK + np.clip(np.asarray(vals, np.float64)[np.clip(idx, 0, w - 1)], 0, 255) * F_RANGE / 255
        t += ms

    for ms, f in (PREAMBLE if preamble else []):
        tone(ms, f)
    for ms, f in vis_tones(mode.vis):
        tone(ms, f)
    for _, ms, f in mode.pre:
        tone(ms, f)
    for g, ch in enumerate(mode_channels(mode, rgb)):
        for kind, ms, arg in mode.segs:
            if kind == "t":
                tone(ms, arg)
            else:
                scan(ms, ch[arg])
        if progress and g % 4 == 0:
            progress(g)
    end = int(round(t * fs / 1000))
    freq = freq[:end + int(0.01 * fs)]
    phase = np.cumsum(2 * np.pi * freq / fs)
    out = np.sin(phase) * amp
    ramp = int(0.005 * fs)                    # 5 ms in- en uitfaden: geen klikken
    env = np.ones(len(out))
    env[:ramp] = np.linspace(0, 1, ramp)
    env[-ramp:] = np.linspace(1, 0, ramp)
    return (out * env).astype(np.float32)


# ----------------------------------------------------------------- decoder
def fir_lowpass(n, fc, fs):
    n = int(n) | 1
    k = np.arange(n) - (n - 1) / 2
    h = np.sinc(2 * fc / fs * k) * np.blackman(n)
    return (h / h.sum()).astype(np.float32)


def moving_avg(x, n):
    if n <= 1:
        return x
    c = np.cumsum(np.concatenate([[0.0], x.astype(np.float64)]))
    return ((c[n:] - c[:-n]) / n).astype(np.float32)


class SSTVDecoder:
    """
    Streaming SSTV-decoder.
    Audio -> mengen naar basisband rond 1900 Hz -> FIR-laagdoorlaat -> decimeren tot ~12 kHz ->
    momentane frequentie.  Daarop: VIS-detectie (met afstemfout-correctie), syncpuls-detectie,
    lijn-voor-lijn beeldopbouw en automatische scheefstand-correctie (slant) via een lineaire fit
    van de syncpulsen.  Zonder VIS kan hij ook starten op een reeks regelmatige syncpulsen.
    """
    BUF_S = 420                      # seconden frequentiegeschiedenis (PD 290 duurt ~290 s)
    SYNC_THR = 1350.0

    def __init__(self, fs, on_event=None):
        self.fs = fs
        self.D = max(1, int(round(fs / 12000)))
        self.wfs = fs / self.D
        self.spm = self.wfs / 1000.0                 # samples per ms
        # twee trappen: grof anti-alias-filter op fs, daarna een scherp kanaalfilter van +/-750 Hz
        # rond 1750 Hz (doorlaat ca. 1000-2500 Hz) op de lage samplefrequentie
        self.h = fir_lowpass(int(fs * 0.0015), 2200, fs)
        self.h2_len = int(self.wfs * 0.02) | 1        # vaste lengte: zelfde vertraging voor elk filter
        self.rx_filter = "normal"
        self.pix_widen = 1.0
        self.denoise = False
        self.h2 = fir_lowpass(self.h2_len, 760, self.wfs)
        self.tail = np.zeros(len(self.h) - 1, dtype=np.complex64)
        self.tail2 = np.zeros(len(self.h2) - 1, dtype=np.complex64)
        self.lo_ph = 0.0
        self.dec_off = 0
        self.zprev = np.complex64(1)
        self.cap = int(self.wfs * self.BUF_S)
        self.f = np.zeros(self.cap, dtype=np.float32)
        self.p = np.zeros(self.cap, dtype=np.complex64)  # discriminator z[n]*conj(z[n-1]) (gewogen middelen)
        self.base = 0                                # absolute index van f[0]
        self.n = 0                                   # aantal geldige samples in f
        self.on_event = on_event or (lambda *a: None)
        self.lock = threading.RLock()
        # sync-detectie
        self.sm_n = max(3, int(round(1.5 * self.spm)))
        self.sync_pos = 0                            # absolute index t/m waar gezocht is
        self.in_pulse = False
        self.pulse_start = 0
        self.pulses = deque(maxlen=400)              # (start, eind) absolute work-samples
        # VIS
        self.bin_n = max(1, int(self.spm))           # samples per bin (~1 ms)
        self.bin_ms = self.bin_n / self.spm
        self.bins = deque(maxlen=int(1200 / self.bin_ms))
        self.bin_pos = 0                             # absolute sample-index van de volgende bin
        self.bin_idx = 0                             # absolute index van de volgende bin
        self.vis_hold = 0
        # instellingen
        self.sync_thr = self.SYNC_THR
        self.pulse_rate = deque(maxlen=64)            # eindtijden recente pulsen (diagnose)
        self.forced = None                           # Mode of None (= automatisch)
        self.freerun = True
        self.slant = True
        self.muted = False
        self.level = 0.0
        self.snr = 0.0
        self.last_vis = None
        self._reset_rx()

    # ---------- toestand
    def _reset_rx(self):
        self.mode = None
        self.state = "idle"
        self.img = None
        self.g_next = 0
        self.t0 = 0.0
        self.period = 0.0
        self.offset = 0.0
        self.fit_pts = []
        self.fitted = False
        self.fit_idx = 0
        self.last_sync = 0
        self.slant_ppm = 0.0
        self.comp = 1.0                 # ruis drukt de gemeten zwaai samen naar 1750 Hz; 1.0 = geen correctie
        self.sync_f = deque(maxlen=40)  # gemeten frequentie in geaccepteerde syncpulsen
        self.via_vis = False
        self.sync_ok = 0
        self.sync_seen = 0
        self._last_rr = 0

    FILTERS = {"wide": (1250, 1.0), "normal": (760, 1.0), "narrow": (760, 1.5)}

    def set_filter(self, name):
        """Ontvangstfilter: 'wide' (scherpst, sterke signalen), 'normal', 'narrow' (minste ruis, zwak signaal)."""
        fc, widen = self.FILTERS.get(name, self.FILTERS["normal"])
        with self.lock:
            self.rx_filter = name
            self.h2 = fir_lowpass(self.h2_len, fc, self.wfs)
            self.pix_widen = widen

    @property
    def now(self):
        return self.base + self.n

    def stop(self, save=True):
        """RX handmatig stoppen; bij voldoende regels wordt het (deel)beeld afgeleverd."""
        with self.lock:
            if self.state == "rx":
                self._finish(complete=False, save=save)

    def set_forced(self, mode):
        with self.lock:
            self.forced = mode

    # ---------- signaalverwerking
    def process(self, x):
        if self.muted:
            return
        x = np.asarray(x, dtype=np.float32)
        n = len(x)
        rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if n else 0.0
        self.level = 0.9 * self.level + 0.1 * rms
        ph = self.lo_ph + 2 * np.pi * F_CENTER / self.fs * np.arange(n)
        self.lo_ph = float((self.lo_ph + 2 * np.pi * F_CENTER / self.fs * n) % (2 * np.pi))
        z = (x * np.exp(-1j * ph)).astype(np.complex64)
        zz = np.concatenate([self.tail, z])
        self.tail = zz[-(len(self.h) - 1):]
        y = np.convolve(zz, self.h, mode="valid")
        y = y[self.dec_off::self.D]
        self.dec_off = (self.dec_off - n) % self.D
        if not len(y):
            return
        yy = np.concatenate([self.tail2, y.astype(np.complex64)])
        self.tail2 = yy[-(len(self.h2) - 1):]
        y = np.convolve(yy, self.h2, mode="valid").astype(np.complex64)
        prev = np.concatenate([[self.zprev], y[:-1]])
        self.zprev = y[-1]
        pz = (y * np.conj(prev)).astype(np.complex64)
        fr = (F_CENTER + np.angle(pz) * self.wfs / (2 * np.pi)).astype(np.float32)
        with self.lock:
            self._append(fr, pz)
            self._scan_sync()
            self._scan_vis()
            if self.state == "rx":
                self._fit()
                self._render_ready()
            elif self.freerun:
                self._try_freerun()

    def _append(self, fr, pz):
        m = len(fr)
        if self.n + m > self.cap:
            keep_from = self.now - int(self.cap * 0.6)
            if self.state == "rx":
                keep_from = min(keep_from, int(self.t0) - int(2 * self.wfs))
            drop = max(0, min(keep_from - self.base, self.n))
            if self.n + m - drop > self.cap:
                drop = self.n + m - self.cap
            self.f[:self.n - drop] = self.f[drop:self.n]
            self.p[:self.n - drop] = self.p[drop:self.n]
            self.base += drop
            self.n -= drop
        self.f[self.n:self.n + m] = fr
        self.p[self.n:self.n + m] = pz
        self.n += m

    def get(self, a, b):
        """Frequenties voor absolute indices [a, b) (buiten de buffer: zwart)."""
        a, b = int(a), int(b)
        out = np.full(max(b - a, 0), F_BLACK, dtype=np.float32)
        lo, hi = max(a, self.base), min(b, self.now)
        if hi > lo:
            out[lo - a:hi - a] = self.f[lo - self.base:hi - self.base]
        return out

    def get_p(self, a, b):
        """Discriminatorwaarden voor [a, b); buiten de buffer: een zwak 'zwart' signaal."""
        a, b = int(a), int(b)
        blk = np.complex64(1e-4 * np.exp(2j * np.pi * (F_BLACK - F_CENTER) / self.wfs))
        out = np.full(max(b - a, 0), blk, dtype=np.complex64)
        lo, hi = max(a, self.base), min(b, self.now)
        if hi > lo:
            out[lo - a:hi - a] = self.p[lo - self.base:hi - self.base]
        return out

    def p2f(self, z):
        return F_CENTER + np.angle(z) * self.wfs / (2 * np.pi)

    def smooth_f(self, a, b, L):
        """Frequentie gemiddeld over L samples (amplitude-gewogen, dus ongevoelig voor ruispieken).
        Element i hoort bij sample a + i + L//2."""
        z = self.get_p(a, b).astype(np.complex128)
        if len(z) < L:
            return np.zeros(0, np.float32)
        c = np.concatenate([[0], np.cumsum(z)])
        return self.p2f(c[L:] - c[:-L]).astype(np.float32)

    def present(self, seconds=2.0):
        """Is er (nog) een SSTV-achtig signaal?  Een toon geeft een rustige momentane frequentie,
        ruis springt van sample tot sample (mediaan |verschil|: ruis ~80 Hz, SSTV bij 0 dB ~60 Hz)."""
        n = int(seconds * self.wfs)
        if self.n < n:
            return False
        return float(np.median(np.abs(np.diff(self.f[self.n - n:self.n])))) < 72.0

    def quality(self, seconds=1.5):
        """Signaalkwaliteit 0..1: coherentie van de discriminator over 2 ms (toon ~1, ruis ~0,2-0,3)."""
        n = int(seconds * self.wfs)
        if self.n < n:
            return 0.0
        z = self.p[self.n - n:self.n]
        L = max(4, int(2 * self.spm))
        m = len(z) // L * L
        z = z[:m].reshape(-1, L)
        num = np.abs(z.sum(axis=1))
        den = np.abs(z).sum(axis=1) + 1e-12
        return float(np.mean(num / den))

    # ---------- syncpulsen
    def _scan_sync(self):
        L = self.sm_n
        a = max(self.sync_pos - L, self.base)
        if self.now - a < L + 2:
            return
        sm = self.smooth_f(a, self.now, L)       # sm[i] hoort bij sample a + i + L//2
        below = sm < self.sync_thr
        start = self.sync_pos - (a + L // 2)
        start = max(start, 0)
        idx0 = a + L // 2
        b = below[start:]
        if not len(b):
            return
        ch = np.flatnonzero(np.diff(np.concatenate([[self.in_pulse], b]).astype(np.int8)))
        for c in ch:
            pos = idx0 + start + int(c)
            if not self.in_pulse:
                self.in_pulse, self.pulse_start = True, pos
            else:
                self.in_pulse = False
                dur = (pos - self.pulse_start) / self.spm
                if 2.5 <= dur <= 30:
                    self.pulses.append((self.pulse_start, pos))
                    self.pulse_rate.append(pos)
        self.sync_pos = idx0 + start + len(b)

    # ---------- VIS
    def _scan_vis(self):
        while self.bin_pos + self.bin_n <= self.now:
            if self.bin_pos >= self.base:
                seg = self.p[self.bin_pos - self.base:self.bin_pos - self.base + self.bin_n]
                self.bins.append(complex(seg.sum()))      # complexe som: later per VIS-bit optellen
            else:
                self.bins.append(0j)
            self.bin_pos += self.bin_n
            self.bin_idx += 1
            if self.vis_hold > 0:
                self.vis_hold -= 1
                continue
            r = self._vis_check()
            if r:
                self._vis_found(*r)

    def _vis_check(self):
        b30 = int(round(30 / self.bin_ms))
        b300 = int(round(300 / self.bin_ms))
        need = 10 * b30 + b300
        if len(self.bins) < need:
            return None
        bins = self.bins
        L = len(bins)

        def seg_med(i0, i1):                     # i0/i1 relatief t.o.v. einde (negatief)
            # frequentie van een VIS-segment: complexe som over het segment (zonder 5 ms flanken)
            k = max(1, int(round(5 / self.bin_ms)))
            z = sum(bins[L + i] for i in range(i0 + k, i1 - k))
            return float(self.p2f(z)) if z else 0.0
        stop = seg_med(-b30, 0)
        if not (900 < stop < 1600):
            return None
        start = seg_med(-10 * b30, -9 * b30)
        if not (900 < start < 1600):
            return None
        leader = seg_med(-10 * b30 - b300 + 2 * b30, -10 * b30)
        if not (1600 < leader < 2200):
            return None
        # Bij veel ruis trekt de ruis de gemeten frequenties naar het midden (1750 Hz) toe.
        # Model: gemeten = 1750 + c * (echt + afstemfout - 1750).  Leader (1900) en stopbit (1200)
        # geven c en de afstemfout; daarmee worden start- en databits gecorrigeerd.
        sync_m = (stop + start) / 2 if abs(stop - start) < 120 else stop
        c = (leader - sync_m) / (F_LEADER - F_SYNC)
        if not (0.35 < c < 1.15):
            return None
        off = (leader - F_CENTER) / c + F_CENTER - F_LEADER
        if abs(off) > 250:
            return None

        def corr(v):
            return F_CENTER + (v - F_CENTER) / c - off
        if abs(corr(stop) - F_SYNC) > 70 or abs(corr(start) - F_SYNC) > 90:
            return None
        bits = []
        for i in range(8):
            v = corr(seg_med(-(9 - i) * b30, -(8 - i) * b30))
            if abs(v - 1100) < 80:
                bits.append(1)
            elif abs(v - 1300) < 80:
                bits.append(0)
            else:
                return None
        code = sum(b << i for i, b in enumerate(bits[:7]))
        if (sum(bits[:7]) + bits[7]) % 2:
            return None
        mode = MODE_BY_VIS.get(code)
        if mode is None:
            return None
        self._vis_c = c
        return mode, off

    def _vis_found(self, mode, off):
        # flank leader -> startbit nauwkeurig zoeken
        end_est = self.bin_pos
        exp_edge = end_est - 300 * self.spm
        a = int(exp_edge - 25 * self.spm)
        seg = self.smooth_f(a, int(exp_edge + 25 * self.spm), max(3, int(self.spm)))
        thr = (F_LEADER + F_SYNC) / 2 + off
        k = np.flatnonzero((seg[:-1] >= thr) & (seg[1:] < thr))
        if len(k):
            best = k[np.argmin(np.abs(k + a + int(self.spm) // 2 - exp_edge))]
            edge = a + int(best) + int(self.spm) // 2 + 0.5
        else:
            edge = exp_edge
        vis_end = edge + 300 * self.spm
        if self.state == "rx":
            if vis_end - self.t0 < 2 * self.wfs:
                return
            self._finish(complete=False)
        use = self.forced or mode
        self.last_vis = mode
        self._start(use, vis_end + use.pre_ms * self.spm, off, via_vis=True)
        self.vis_hold = int(600 / self.bin_ms)

    # ---------- start zonder VIS (vrijloop op syncpulsen)
    def _try_freerun(self):
        if len(self.pulses) < 6:
            return
        recent = list(self.pulses)[-12:]
        if self.now - recent[-1][1] > 2 * self.wfs:
            return
        cands = [self.forced] if self.forced else MODES
        for m in cands:
            sp = m.sync_period_ms * self.spm
            sl = m.sync_len
            ps = [p for p in recent if 0.65 * sl <= (p[1] - p[0]) / self.spm <= 1.45 * sl]
            if len(ps) < 6:
                continue
            ends = np.array([p[1] for p in ps[-8:]], dtype=np.float64)
            k = np.round((ends - ends[0]) / sp)
            err = (ends - ends[0]) - k * sp
            ok = np.abs(err) < max(0.6 * self.spm, 0.0015 * sp)
            if ok.sum() < 6 or len(set(k[ok].tolist())) < 6:
                continue
            kk, ee = k[ok], ends[ok]
            steps = np.diff(kk)
            if np.mean(steps == 1) < 0.6:
                continue
            b = np.polyfit(kk, ee, 1)[0]
            if abs(b / sp - 1) > 0.004:
                continue
            first_end = ee[0]
            so, slen = m.syncs[0]
            t0 = first_end - (so + slen) * self.spm
            if len(m.syncs) > 1:              # Robot 36: welke lijn draagt R-Y?
                t0 = self._robot_parity(m, t0)
            self._start(m, t0, 0.0, via_vis=False)
            return

    def _robot_parity(self, m, t0):
        sep = 9 + 3 + 88
        f = self.get(t0 + (sep + 0.8) * self.spm, t0 + (sep + 3.7) * self.spm)
        if len(f) and float(np.median(f)) > (F_BLACK + F_WHITE) / 2:
            return t0 + 150 * self.spm
        return t0

    # ---------- beeldopbouw
    def _start(self, mode, t0, off, via_vis):
        self._reset_rx()
        self.mode = mode
        self.state = "rx"
        self.t0 = float(t0)
        self.period = mode.group_ms * self.spm
        self.offset = off
        c = getattr(self, "_vis_c", 1.0) if via_vis else 1.0
        # syncdrempel mee met de afstemfout en (bij veel ruis) met de 'samendrukking' naar 1750 Hz
        self.sync_thr = F_CENTER + max(0.5, min(1.0, c)) * (self.SYNC_THR + off - F_CENTER)
        self.via_vis = via_vis
        self.img = np.zeros((mode.height, mode.width, 3), dtype=np.uint8)
        self.last_sync = self.t0
        self.fit_idx = len(self.pulses)
        self.fit_pulses_from = self.t0 - 5 * self.spm
        self.on_event("start", mode.name, via_vis)

    def _fit(self):
        m = self.mode
        sl = m.sync_len
        for (ps, pe) in list(self.pulses):
            if pe <= self.fit_pulses_from:
                continue
            self.fit_pulses_from = pe
            dur = (pe - ps) / self.spm
            if not (0.6 * sl <= dur <= 1.5 * sl):
                continue
            self.sync_seen += 1
            g = math.floor((pe - self.t0) / self.period)
            best = None
            for gg in (g - 1, g, g + 1):
                if gg < 0 or gg >= m.groups:
                    continue
                for so, slen in m.syncs:
                    x = gg * m.group_ms + so + slen
                    pred = self.t0 + x * self.period / m.group_ms
                    r = pe - pred
                    if best is None or abs(r) < abs(best[1]):
                        best = (x, r)
            if best is None:
                continue
            el_s = (pe - self.t0) / self.wfs
            tol_ms = 2.5 if self.fitted else min(3.0 + 5.0 * el_s, 0.25 * m.sync_period_ms)
            if abs(best[1]) < tol_ms * self.spm:
                self.fit_pts.append((best[0], pe))
                self.last_sync = pe
                self.sync_ok += 1
                k = int(0.8 * self.spm)
                if pe - ps > 3 * k:
                    self.sync_f.append(float(np.median(self.get(ps + k, pe - k))))
                    if len(self.sync_f) >= 8:
                        med = float(np.median(self.sync_f))
                        c = (med - F_CENTER) / (F_SYNC + self.offset - F_CENTER)
                        # kleine afwijkingen zijn meetruis (VIS-offset, pulsflanken): pas alleen echte
                        # samendrukking aan, zodat schone signalen onaangeroerd blijven
                        self.comp = min(max(c, 0.60), 1.0) if c < 0.985 else 1.0
        if not self.slant or len(self.fit_pts) < 6:
            return
        pts = np.array(self.fit_pts[-400:], dtype=np.float64)
        if pts[-1, 0] - pts[0, 0] < 5 * m.group_ms:
            return
        x, y = pts[:, 0], pts[:, 1]
        for _ in range(2):
            b, a = np.polyfit(x, y, 1)
            res = y - (a + b * x)
            keep = np.abs(res) < 1.5 * self.spm
            if keep.sum() < 6:
                return
            x, y = x[keep], y[keep]
        b, a = np.polyfit(x, y, 1)
        ratio = b / self.spm
        if abs(ratio - 1) > 0.015:
            return
        self.t0 = float(a)
        self.period = b * m.group_ms
        self.slant_ppm = (ratio - 1) * 1e6
        self.fitted = True

    def _render_ready(self):
        m = self.mode
        margin = 3 * self.spm
        while self.g_next < m.groups and self.t0 + (self.g_next + 1) * self.period + margin <= self.now:
            self._render_group(self.g_next)
            self.g_next += 1
        if self.g_next - self._last_rr >= 16 and self.slant:
            self._last_rr = self.g_next
            for g in range(self.g_next):
                self._render_group(g)
        if self.g_next >= m.groups:
            self._finish(complete=True)
            return
        lost = max(14 * m.sync_period_ms * self.spm, 5 * self.wfs)
        quiet = self.now - self.last_sync
        if quiet > lost and self.now - self.t0 > lost:
            # syncpulsen kwijt: bij zwak/fadend signaal (nog wel SSTV-achtig) doorgaan, max. 30 s;
            # is er alleen nog ruis, dan stoppen
            if quiet > 30 * self.wfs or not self.present():
                self._finish(complete=False)

    def _render_group(self, g):
        m = self.mode
        k = self.period / m.group_ms
        tg = self.t0 + g * self.period
        a = int(math.floor(tg)) - 2
        seg = self.get_p(a, int(math.ceil(tg + self.period)) + 3).astype(np.complex128)
        c = np.concatenate([[0.0], np.cumsum(seg)])
        cr, ci = c.real, c.imag
        xi = np.arange(len(c))
        ch = {}
        for off, ms, name in m.scans:
            # pixelvenster: het pixel zelf, minimaal ~1,6 sample breed (anders te veel ruis),
            # en begrensd tot het scansegment, zodat randpixels niet in sync of porch meten
            ctr = tg - a + (off + (np.arange(m.width) + 0.5) * ms / m.width) * k
            half = max(0.5 * ms / m.width * k * self.pix_widen, 0.8)
            lo, hi = tg - a + off * k, tg - a + (off + ms) * k
            e0 = np.clip(ctr - half, lo, None)
            e1 = np.maximum(np.clip(ctr + half, None, hi), e0 + 0.6)
            dz = (np.interp(e1, xi, cr) - np.interp(e0, xi, cr)) + 1j * (np.interp(e1, xi, ci) - np.interp(e0, xi, ci))
            fv = F_CENTER + (self.p2f(dz) - F_CENTER) / self.comp
            ch[name] = np.clip((fv - self.offset - F_BLACK) * 255.0 / F_RANGE, 0, 255)
        if m.color == "rgb":
            rows = [np.clip(np.stack([ch["R"], ch["G"], ch["B"]], -1) + 0.5, 0, 255).astype(np.uint8)]
        elif m.rows == 2:
            rows = [yuv_to_rgb(ch["Y0"], ch["U"], ch["V"]), yuv_to_rgb(ch["Y1"], ch["U"], ch["V"])]
        else:
            rows = [yuv_to_rgb(ch["Y"], ch["U"], ch["V"])]
        for i, row in enumerate(rows):
            self.img[g * m.rows + i] = row

    def _finish(self, complete, save=True):
        m = self.mode
        if self.slant and self.g_next:
            for g in range(self.g_next):
                self._render_group(g)
        lines = self.g_next * m.rows
        img = denoise(self.img) if self.denoise else self.img.copy()
        name = m.name
        self.state = "idle"
        self.sync_thr = self.SYNC_THR
        self.pulses.clear()
        self.on_event("done", img, name, complete, lines if save else 0)
        self.mode = None

    # ---------- status voor de GUI
    def status(self):
        with self.lock:
            m = self.mode
            return dict(state=self.state, mode=m.name if m else "",
                        line=self.g_next * (m.rows if m else 1), lines=m.height if m else 0,
                        offset=self.offset, slant=self.slant_ppm, vis=self.via_vis,
                        sync=(self.sync_ok / max(1, self.g_next * (len(m.syncs) if m else 1))) if m else 0.0,
                        last_vis=self.last_vis.name if self.last_vis else "",
                        pulses_s=sum(1 for p in self.pulse_rate if self.now - p < 5 * self.wfs) / 5.0,
                        tone=self._tone())

    def _tone(self):
        """Mediaan van de gemeten audiofrequentie over de laatste 0,5 s (diagnose: ligt het signaal tussen 1200 en 2300 Hz?)."""
        if self.n < int(0.5 * self.wfs):
            return 0.0
        return float(np.median(self.f[self.n - int(0.5 * self.wfs):self.n]))


# ----------------------------------------------------------------- beeldhulpjes
def denoise(img):
    """Ruisonderdrukking voor SSTV-beelden: 3x3-mediaan per kleur (haalt losse ruispixels weg,
    randen blijven scherp)."""
    a = np.asarray(img)
    if a.ndim != 3 or a.shape[0] < 3 or a.shape[1] < 3:
        return a.copy()
    p = np.pad(a, ((1, 1), (1, 1), (0, 0)), mode="edge")
    H, W = a.shape[:2]
    stack = np.stack([p[y:y + H, x:x + W] for y in range(3) for x in range(3)], axis=0)
    return np.median(stack, axis=0).astype(np.uint8)

def resize_nearest(img, w, h):
    img = np.asarray(img)
    ys = (np.arange(h) * img.shape[0] / h).astype(np.int64)
    xs = (np.arange(w) * img.shape[1] / w).astype(np.int64)
    return img[ys][:, xs]


def resize_area(img, w, h):
    """Eenvoudige oppervlakte-middeling (voor verkleinen) of naaste-buur (vergroten)."""
    img = np.asarray(img, dtype=np.float32)
    H, W = img.shape[:2]
    if w >= W and h >= H:
        return resize_nearest(img, w, h).astype(np.uint8)
    yi = np.minimum((np.arange(H) * h / H).astype(np.int64), h - 1)
    xi = np.minimum((np.arange(W) * w / W).astype(np.int64), w - 1)
    acc = np.zeros((h, W, img.shape[2]), np.float32)
    np.add.at(acc, yi, img)
    cnt_y = np.bincount(yi, minlength=h).astype(np.float32)
    acc /= cnt_y[:, None, None]
    out = np.zeros((h, w, img.shape[2]), np.float32)
    np.add.at(out, (slice(None), xi), acc)
    cnt_x = np.bincount(xi, minlength=w).astype(np.float32)
    out /= cnt_x[None, :, None]
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


# ----------------------------------------------------------------- SSTV herkennen in de spectrumscope
def detect_sstv(frames, hz_per_bin):
    """
    Zoek SSTV-signalen in een reeks scope-spectra (n_frames x n_bins, waarden 0..1).
    Een SSTV-signaal is ~1,1 kHz breed (1200-2300 Hz) en staat onafgebroken aan, met constante
    amplitude.  Spraak (SSB) is breder en heeft pauzes, CW/RTTY/FT8 zijn smal.
    Geeft [(bin_laag, bin_hoog, score)] terug.
    """
    F = np.asarray(frames, dtype=np.float32)
    if F.ndim != 2 or F.shape[0] < 30 or hz_per_bin <= 0 or hz_per_bin > 250:
        return []
    # eerst over ~1/12 van het venster middelen: de SSTV-toon zwaait binnen elke beeldlijn door de
    # hele band, dus in één scope-frame is maar een deel van de band 'aan', gemiddeld de hele band
    B = max(1, F.shape[0] // 12)
    F = F[:F.shape[0] // B * B].reshape(-1, B, F.shape[1]).mean(axis=1)
    floor = np.percentile(F, 30, axis=1)[:, None]
    d = F - floor
    mad = float(np.median(np.abs(d))) + 1e-6
    thr = max(0.03, 4.0 * mad)
    occ = (d > thr).mean(axis=0)
    k = max(1, int(round(80 / hz_per_bin)))
    if k > 1:
        occ = np.convolve(occ, np.ones(k) / k, mode="same")
    steady = occ > 0.55
    out = []
    i, n = 0, len(steady)
    gap = max(1, int(round(120 / hz_per_bin)))
    while i < n:
        if not steady[i]:
            i += 1
            continue
        j = i
        while j < n:
            if steady[j]:
                j += 1
                continue
            g = j
            while g < n and not steady[g] and g - j < gap:
                g += 1
            if g < n and steady[g] and g - j < gap:
                j = g
                continue
            break
        w = (j - i) * hz_per_bin
        if 600 <= w <= 1700:
            pad = max(2, int(round(300 / hz_per_bin)))
            outside = np.concatenate([occ[max(0, i - pad):max(0, i - pad // 3)],
                                      occ[min(n, j + pad // 3):min(n, j + pad)]])
            inside = float(occ[i:j].mean())
            if inside > 0.7 and (not len(outside) or float(outside.mean()) < 0.4):
                out.append((i, j, inside))
        i = j + 1
    return out
