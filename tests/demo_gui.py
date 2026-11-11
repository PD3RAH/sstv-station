"""
GUI-demo/test zonder radio: nep-sounddevice en nep-CI-V.  Speelt een SSTV-signaal (met ruis) af
op de 'RX-audio' en simuleert de IC-7300 (frequentie, modus, S-meter, spectrumscope).
Run:  python tests/demo_gui.py [snelheid] [screenshot.png] [sec]
"""
import os
import sys
import threading
import time
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
os.environ.setdefault("APPDATA", "/tmp/sstvdemo")

SPEED = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
SHOT = sys.argv[2] if len(sys.argv) > 2 else None
SHOT_AT = float(sys.argv[3]) if len(sys.argv) > 3 else 20
SIGNAL = []
SCOPE_HALF = int(os.environ.get("DEMO_SCOPE_HALF", "5000"))
TX_LOG = []


# ---------------- nep-sounddevice
class CallbackStop(Exception):
    pass


class InputStream:
    def __init__(self, device, channels, samplerate, blocksize, dtype, callback):
        self.fs, self.bs, self.cb = samplerate, blocksize, callback
        self.run = False

    def start(self):
        self.run = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        rng = np.random.default_rng(3)
        pos = 0
        t0 = time.time()
        n = 0
        while self.run:
            sig = SIGNAL[0] if SIGNAL else np.zeros(1, np.float32)
            blk = np.zeros(self.bs, np.float32)
            if pos < len(sig):
                part = sig[pos:pos + self.bs]
                blk[:len(part)] = part
            pos += self.bs
            blk += rng.normal(0, 0.02, self.bs).astype(np.float32)
            self.cb(blk[:, None], self.bs, None, None)
            n += 1
            dt = t0 + n * self.bs / self.fs / SPEED - time.time()
            if dt > 0:
                time.sleep(dt)

    def stop(self):
        self.run = False

    def close(self):
        self.run = False


class OutputStream:
    def __init__(self, device, samplerate, channels, dtype, blocksize, callback, finished_callback):
        self.a = (samplerate, channels, blocksize, callback, finished_callback)

    def __enter__(self):
        fs, ch, bs, cb, fin = self.a

        def run():
            out = np.zeros((bs, ch), np.float32)
            while True:
                try:
                    cb(out, bs, None, None)
                    TX_LOG.append(out[:, 0].copy())
                except CallbackStop:
                    break
                time.sleep(bs / fs / SPEED / 4)
            fin()
        threading.Thread(target=run, daemon=True).start()
        return self

    def __exit__(self, *a):
        return False


DEVS = [{"name": "Microphone (2- USB Audio CODEC)", "hostapi": 0, "max_input_channels": 2,
         "max_output_channels": 0, "default_samplerate": 48000.0},
        {"name": "Speakers (2- USB Audio CODEC)", "hostapi": 0, "max_input_channels": 0,
         "max_output_channels": 2, "default_samplerate": 48000.0}]
sd = types.ModuleType("sounddevice")
sd.CallbackStop = CallbackStop
sd.InputStream = InputStream
sd.OutputStream = OutputStream
sd.query_hostapis = lambda: [{"name": "MME"}, {"name": "Windows DirectSound"}, {"name": "Windows WASAPI"}]
if os.environ.get("DEMO_MANYDEV"):
    for api in (0, 1, 2):
        for nm in ("Microfoon (Fresh 'n Rebel 3AGH301)", "Microfoon (Realtek(R) Audio)", "Stereo-mix (Realtek(R) Audio)"):
            DEVS.append({"name": nm, "hostapi": api, "max_input_channels": 2, "max_output_channels": 0,
                         "default_samplerate": 48000.0})
        for nm in ("Luidsprekers (Realtek(R) Audio)", "Hoofdtelefoon (Fresh 'n Rebel 3AGH301)"):
            DEVS.append({"name": nm, "hostapi": api, "max_input_channels": 0, "max_output_channels": 2,
                         "default_samplerate": 48000.0})
    DEVS.append({"name": "Microsoft Geluidstoewijzing - Input", "hostapi": 0, "max_input_channels": 2,
                 "max_output_channels": 0, "default_samplerate": 44100.0})
    DEVS.append({"name": "Microfoon (2- USB Audio CODEC )", "hostapi": 1, "max_input_channels": 2,
                 "max_output_channels": 0, "default_samplerate": 48000.0})
sd.query_devices = lambda device=None, kind=None: DEVS if device is None else DEVS[device]
sd.check_input_settings = lambda **k: None
sd.check_output_settings = lambda **k: None
sys.modules["sounddevice"] = sd

import sstv  # noqa: E402
import sstv_station as S  # noqa: E402


# ---------------- nep-CI-V
class FakeCIV:
    def __init__(self, app):
        self.app, self.addr = app, 0x94
        self.freq, self.mode, self.data, self.filt = 14230000, 0x01, 1, 1
        self.running = True
        threading.Thread(target=self._scope, daemon=True).start()

    def send_to(self, addr, cmd, data=b""):
        self.send(cmd, data)

    def send(self, cmd, data=b""):
        f = self.app._on_frame
        data = bytes(data)
        if cmd == 0x19:
            f(0x19, b"\x00\x94", 0x94)
        elif cmd == 0x03:
            f(0x03, S.int_to_bcd_le(self.freq, 5), 0x94)
        elif cmd == 0x05:
            self.freq = S.bcd_le_to_int(data[:5])
        elif cmd == 0x04:
            f(0x04, bytes([self.mode, self.filt]), 0x94)
        elif cmd == 0x06:
            self.mode, self.filt = data[0], data[1]
        elif cmd == 0x1A and data[:1] == b"\x06":
            if len(data) >= 3:
                self.data = data[1]
            f(0x1A, bytes([0x06, self.data, self.filt]), 0x94)
        elif cmd == 0x1A and data == b"\x03":
            f(0x1A, b"\x03\x31", 0x94)                      # index 31 = 2700 Hz
        elif cmd == 0x15 and data == b"\x02":
            v = 110 + int(10 * np.random.rand())
            f(0x15, bytes([0x02, v // 100, ((v % 100) // 10) << 4 | v % 10]), 0x94)
        elif cmd == 0x15 and data == b"\x15":
            f(0x15, b"\x15\x02\x15", 0x94)
        elif cmd == 0x1C and data == b"\x00\x01":
            TX_LOG.append("PTT ON")
        elif cmd == 0x1C and data == b"\x00\x00":
            TX_LOG.append("PTT OFF")

    def _scope(self):
        rng = np.random.default_rng(7)
        x = np.arange(475)
        while self.running:
            v = 20 + rng.gamma(2, 6, 475)
            span = 2 * SCOPE_HALF
            hz = span / 475
            st = self.freq - SCOPE_HALF
            # SSTV-station op 14.2265 USB: band 14.2277-14.2288, de toon zwaait door de band
            lo, hi = int((14227700 - st) / hz), int((14228800 - st) / hz)
            if 0 <= lo < hi < 475:
                w = hi - lo
                a = lo + rng.integers(0, max(1, w - w // 4))
                v[a:a + max(1, w // 4)] += 55
                v[lo:hi] += 12
            # SSB-spraak 14.2320-14.2344 met pauzes
            if rng.random() < 0.6:
                a, b = int((14232000 - st) / hz), int((14234400 - st) / hz)
                if 0 <= a < b < 475:
                    v[a:b] += 35 * rng.random(b - a)
            # draaggolf
            c = int((14235200 - st) / hz)
            if 0 <= c < 475:
                v[c:c + 2] += 70
            v = np.clip(v, 0, 160).astype(np.uint8)
            hdr = bytes([0x00, 0x00, 0x01, 0x02, 0x00]) + S.int_to_bcd_le(self.freq, 5) + \
                S.int_to_bcd_le(SCOPE_HALF, 5) + b"\x00"
            self.app._on_frame(0x27, hdr + bytes(v[:237]), 0x94)
            self.app._on_frame(0x27, bytes([0x00, 0x00, 0x02, 0x02]) + bytes(v[237:]), 0x94)
            time.sleep(0.07)

    def close(self):
        self.running = False


def scene(w, h, kind=0):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        f = y / h
        col = (int(30 + 200 * f), int(40 + 90 * f), int(110 - 40 * f)) if kind == 0 else \
            (int(20 + 40 * f), int(90 + 100 * f), int(170 + 60 * f))
        d.line([(0, y), (w, y)], fill=col)
    if kind == 0:
        d.ellipse([w * 0.58, h * 0.42, w * 0.80, h * 0.69], fill=(255, 190, 70))
        d.polygon([(0, h), (0, h * 0.78), (w * 0.3, h * 0.66), (w * 0.62, h * 0.8), (w, h * 0.7), (w, h)], fill=(25, 20, 35))
        mx = w * 0.25
        d.line([(mx, h * 0.72), (mx, h * 0.25)], fill=(10, 10, 15), width=max(2, w // 160))
        for i in range(6):
            y = h * (0.28 + i * 0.035)
            half = w * (0.12 - i * 0.012)
            d.line([(mx - half, y), (mx + half, y)], fill=(10, 10, 15), width=max(1, w // 320))
    else:
        d.rectangle([0, h * 0.75, w, h], fill=(40, 120, 60))
        for i, c in enumerate([(230, 60, 60), (240, 200, 60), (70, 160, 230)]):
            d.rectangle([w * (0.15 + i * 0.25), h * 0.45, w * (0.32 + i * 0.25), h * 0.75], fill=c)
    return im


def main():
    import tkinter as tk
    root = tk.Tk()
    root.geometry("1560x1000+0+0")
    app = S.App(root)
    app.cfg.update(mycall="PD3RAH", myname="Richard", myqth="West-Friesland")
    app.lang = "en" if os.environ.get("DEMO_LANG") == "en" else "nl"
    # nep-radio verbinden
    app.civ = FakeCIV(app)
    app._radio_found(0x94)
    # TX-beeld
    app.tx_src = scene(640, 512, 0)
    app.cfg["tx_image"] = "sunset_antenna.jpg"
    app.rebuild()
    # ontvangstsignaal: Scottie S1 van een ander testbeeld
    m = sstv.MODE_BY_KEY[os.environ.get("DEMO_MODE", "S1")]
    src = S.compose_tx(m, scene(m.width, m.height, 1), ["CQ SSTV", "TEST DE PD3RAH", "JO22"])
    a = sstv.encode(m, src, 48000, amp=0.3)
    SIGNAL.append(np.concatenate([np.zeros(48000, np.float32), a]))
    app.toggle_audio()
    if SHOT:
        def grab():
            from PIL import ImageGrab
            root.update()
            ImageGrab.grab(bbox=(0, 0, root.winfo_width(), root.winfo_height())).save(SHOT)
            print("screenshot", SHOT, "lost", app.audio.lost, "q", app.audio.q.qsize(), flush=True)
            if os.environ.get("DEMO_TX"):
                app.send_image()

                def shot_tx():
                    root.update()
                    ImageGrab.grab(bbox=(0, 0, root.winfo_width(), root.winfo_height())).save(SHOT.replace(".png", "_tx.png"))

                def wait_tx():
                    if app.sender.busy or not TX_LOG:
                        root.after(200, wait_tx)
                        return
                    print("TX log:", [x for x in TX_LOG if isinstance(x, str)], flush=True)
                    audio = np.concatenate([x for x in TX_LOG if not isinstance(x, str)])
                    ev = []
                    dec = sstv.SSTVDecoder(48000, lambda *e: ev.append(e))
                    for i in range(0, len(audio), 480):
                        dec.process(audio[i:i + 480])
                    dec.process(np.zeros(48000 * 3, np.float32))
                    done = [e for e in ev if e[0] == "done"]
                    if done:
                        from PIL import Image
                        Image.fromarray(done[-1][1]).save(SHOT.replace(".png", "_txdecoded.png"))
                        err = np.abs(done[-1][1].astype(int) - app.tx_img.astype(int)).mean()
                        print("TX decoded:", done[-1][2], done[-1][3], "mean err", round(err, 2), flush=True)
                    else:
                        print("TX NOT decoded", ev[:3], flush=True)
                    root.destroy()
                root.after(2500, shot_tx)
                root.after(500, wait_tx)
            else:
                root.destroy()
        root.after(int(SHOT_AT * 1000), grab)
    root.mainloop()


if __name__ == "__main__":
    main()
