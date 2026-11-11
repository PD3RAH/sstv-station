"""Loopback-test: encode -> (ruis, klokfout, afstemfout) -> decode, voor alle modi.
Run:  python tests/test_loopback.py  [modus ...]"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import sstv  # noqa: E402


def test_image(w, h):
    y, x = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 3), np.uint8)
    bars = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0)]
    for i, c in enumerate(bars):
        img[: h // 2, i * w // 8:(i + 1) * w // 8] = c
    img[h // 2:, :, 0] = (x[h // 2:] * 255 // (w - 1))
    img[h // 2:, :, 1] = (y[h // 2:] * 255 // (h - 1))
    img[h // 2:, :, 2] = 128
    img[h * 3 // 4: h * 3 // 4 + 4] = 255
    return img


def shift_freq(x, df, fs):
    from scipy.signal import hilbert
    a = hilbert(x)
    return np.real(a * np.exp(2j * np.pi * df * np.arange(len(x)) / fs)).astype(np.float32)


def resample(x, ratio):
    n = int(len(x) * ratio)
    return np.interp(np.arange(n) / ratio, np.arange(len(x)), x).astype(np.float32)


def run(mode, snr_db=20, clock=1.0, df=0.0, fs_rx=48000, vis=True, lead_s=1.3):
    img = test_image(mode.width, mode.height)
    a = sstv.encode(mode, img, fs=48000, amp=0.5, preamble=vis)
    if not vis:                               # VIS eraf knippen
        cut = int((sstv.tx_duration(mode, False) - mode.seconds) * 48000)
        a = a[cut:]
    if df:
        a = shift_freq(a, df, 48000)
    if fs_rx != 48000 or clock != 1.0:
        a = resample(a, fs_rx / 48000 * clock)
    rng = np.random.default_rng(1)
    sig = np.concatenate([np.zeros(int(lead_s * fs_rx), np.float32), a, np.zeros(int(2 * fs_rx), np.float32)])
    p = np.mean(a ** 2)
    noise = rng.normal(0, np.sqrt(p / 10 ** (snr_db / 10) * (fs_rx / 2) / 3000), len(sig)).astype(np.float32)
    sig = sig + noise
    ev = []
    dec = sstv.SSTVDecoder(fs_rx, lambda *e: ev.append(e))
    blk = fs_rx // 100
    t = time.time()
    for i in range(0, len(sig), blk):
        dec.process(sig[i:i + blk])
    el = time.time() - t
    done = [e for e in ev if e[0] == "done"]
    starts = [e for e in ev if e[0] == "start"]
    if not done:
        return None, starts, el, dec
    out = done[-1][1]
    err = np.abs(out.astype(int) - img.astype(int)).mean()
    return err, starts, el, done[-1]


if __name__ == "__main__":
    keys = sys.argv[1:] or [m.key for m in sstv.MODES]
    for k in keys:
        m = sstv.MODE_BY_KEY[k]
        for case in (dict(), dict(snr_db=8), dict(clock=1.0006, df=35), dict(fs_rx=44100), dict(vis=False)):
            err, starts, el, d = run(m, **case)
            info = d[2:] if isinstance(d, tuple) else ""
            print(f"{m.name:15s} {str(case):32s} err={err if err is None else round(err, 1)}  "
                  f"starts={[s[1:] for s in starts]} {info} cpu={el / m.seconds * 100:.0f}%")
