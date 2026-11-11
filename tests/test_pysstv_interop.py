"""Interoperabiliteit: signalen van een onafhankelijke encoder (pip install pysstv) decoderen."""
import sys, numpy as np
import os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'src')); sys.path.insert(0, HERE)
import sstv, test_loopback as T
from PIL import Image
from pysstv import color
# (pysstv codeert Martin M2 met 160 pixels breed; MMSSTV/QSSTV en SSTV Station gebruiken 320)
pairs = [("MartinM1","M1"),("ScottieS1","S1"),("ScottieS2","S2"),("ScottieDX","SDX"),
         ("Robot36","R36"),("PD90","PD90"),("PD120","PD120"),("PD180","PD180"),("WraaseSC2180","SC2180")]
for cls, key in pairs:
    C = getattr(color, cls, None)
    if C is None: print(cls, "not in pysstv"); continue
    m = sstv.MODE_BY_KEY[key]
    img = T.test_image(m.width, m.height)
    enc = C(Image.fromarray(img), 48000, 16)
    a = np.array(list(enc.gen_samples()), dtype=np.float32) / 32768 * 0.5
    sig = np.concatenate([np.zeros(48000, np.float32), a, np.zeros(96000, np.float32)])
    sig += np.random.default_rng(0).normal(0, 0.01, len(sig)).astype(np.float32)
    ev = []
    d = sstv.SSTVDecoder(48000, lambda *e: ev.append(e))
    for i in range(0, len(sig), 480): d.process(sig[i:i+480])
    done = [e for e in ev if e[0]=="done"]
    if done:
        err = np.abs(done[-1][1].astype(int)-img.astype(int)).mean()
        print(f"{cls:14s} -> {[e[1:] for e in ev if e[0]=='start']} complete={done[-1][3]} err={err:.1f}")

    else:
        print(cls, "NOT decoded", ev[:2])
