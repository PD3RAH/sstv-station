"""
GUI-test (zonder radio) voor automatisch starten en de update met één klik.
- radio 'staat uit' bij het opstarten -> programma blijft opnieuw proberen -> radio 'aan' -> verbindt en decodeert
- nep-release op GitHub -> update-exe downloaden -> stil installeren starten -> programma sluit af
Run:  python tests/test_autostart_update.py   (Windows of Linux met X / Xvfb)
"""
import json
import os
import subprocess
import sys
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["APPDATA"] = "/tmp/sstvtest_auto"
import demo_gui as D  # noqa: E402  (installeert nep-sounddevice)

S = D.S
sstv = D.sstv
np = D.np
RADIO_ON = [False]
LOG = []


class SerialStub:
    pass


def fake_civ(port, baud, addr, cb):
    if not RADIO_ON[0]:
        raise OSError(f"could not open port {port}")
    LOG.append("civ open")
    return FAKE[0]


FAKE = []
S.serial = SerialStub
S.CIV = fake_civ

REL = {"tag_name": "v1.0.1", "html_url": "https://github.com/PD3RAH/sstv-station/releases/tag/v1.0.1",
       "assets": [{"name": "SSTVStation-Setup-1.0.1.exe", "browser_download_url": "https://x/SSTVStation-Setup-1.0.1.exe"},
                  {"name": "SSTVStation-Update-1.0.1.exe", "browser_download_url": "https://x/SSTVStation-Update-1.0.1.exe"}]}


def fake_http(url, timeout=8, accept=""):
    LOG.append(("GET", url, accept))
    if url == S.RELEASE_API:
        return json.dumps(REL).encode()
    return b"MZ" + b"\0" * 200_000


S.http_get = fake_http
S.sys.platform = "win32"
POPEN = []
subprocess.Popen = lambda args, **k: POPEN.append(args)
S.messagebox.askyesnocancel = lambda *a, **k: True        # "Ja, nu bijwerken"
S.messagebox.askyesno = lambda *a, **k: True


def main():
    root = tk.Tk()
    root.geometry("1560x1000+0+0")
    app = S.App(root)
    app.cfg.update(mycall="PD3RAH")
    FAKE.append(D.FakeCIV(app))
    m = sstv.MODE_BY_KEY["R36"]
    D.SIGNAL.append(np.concatenate([np.zeros(48000 * 12, np.float32),
                                    sstv.encode(m, np.asarray(D.scene(m.width, m.height, 1)), 48000, amp=0.3)]))
    D.SPEED = 6.0
    root.after(800, app.autostart)
    checks = {}

    def t1():   # radio uit: geen verbinding, audio wel (codec in deze test altijd aanwezig)
        checks["off_civ"] = app.civ is None
        checks["audio"] = app.audio is not None
        checks["status"] = app.status.cget("text")
        RADIO_ON[0] = True

    def t2():   # na de herhaalpoging: verbonden; decoder heeft Robot 36 binnen
        checks["on_civ"] = app.civ is not None
        checks["rx_saved"] = [g[1] for g in app.gallery]
        app.check_update_async()

    def t3():
        checks["upd_info"] = app.update_info

    root.after(3000, t1)
    root.after(12500, t2)
    root.after(14000, t3)
    root.after(16000, lambda: root.destroy() if root.winfo_exists() else None)
    try:
        root.mainloop()
    except tk.TclError:
        pass
    print("radio uit -> niet verbonden:", checks.get("off_civ"), "| status:", checks.get("status"))
    print("audio automatisch gestart :", checks.get("audio"))
    print("radio aan -> verbonden    :", checks.get("on_civ"), LOG.count("civ open"))
    print("update gevonden           :", checks.get("upd_info"))
    print("ontvangen beelden         :", checks.get("rx_saved"))
    print("update gestart met        :", POPEN)
    print("downloads                 :", [x for x in LOG if isinstance(x, tuple)])


if __name__ == "__main__":
    main()
