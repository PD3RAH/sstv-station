# Third-party notices

SSTV Station itself (`src/`, `installer/`) is © 2026 Richard A. Hendriksen (PD3RAH) and
released under the MIT License (see `LICENSE`).

The **source code** only uses third-party libraries at run time; it does not contain
copied third-party code. The **Windows installer** (`SSTVStation-Setup.exe`) bundles the
components below so that no separate Python installation is needed. Their full license
texts are included in the `licenses/` folder of this repository and are installed with
the program.

| Component | Version | License | Source |
|---|---|---|---|
| CPython (Python runtime) | 3.12 | Python Software Foundation License 2.0 | python.org, via conda-forge |
| Tcl/Tk (tkinter GUI) | 8.6.13 | Tcl/Tk license (BSD-style) | tcl.tk, via conda-forge |
| libffi | 3.x | MIT | sourceware.org/libffi |
| zlib | 1.3 | zlib License | zlib.net |
| bzip2 | 1.0.8 | bzip2 License (BSD-style) | gitlab.com/bzip2 |
| xz / liblzma | 5.x | 0BSD | tukaani.org/xz |
| libexpat | 2.x | MIT | libexpat.github.io |
| Microsoft Visual C++ runtime (`vcruntime140.dll`, `vcruntime140_1.dll`) | 14.x | Microsoft Visual C++ Runtime license terms | Microsoft, via conda-forge `vc14_runtime` |
| NumPy (incl. bundled OpenBLAS and GCC runtime) | 2.5 | BSD-3-Clause and others, see `licenses/numpy/` | numpy.org |
| pyserial | 3.5 | BSD-3-Clause | github.com/pyserial/pyserial |
| python-sounddevice | 0.5 | MIT | github.com/spatialaudio/python-sounddevice |
| PortAudio (bundled in sounddevice) | 19.x | MIT | portaudio.com |
| cffi | 2.x | MIT-0 | cffi.readthedocs.io |
| pycparser | 3.x | BSD-3-Clause | github.com/eliben/pycparser |
| Pillow (incl. bundled libjpeg-turbo, libpng, zlib, FreeType, libtiff, libwebp, LittleCMS, OpenJPEG, HarfBuzz, Brotli and others) | 13.0 | MIT-CMU (HPND) and the licenses of the bundled libraries, see `licenses/pillow/LICENSE` | python-pillow.org |
| NSIS (installer framework) | 3.x | zlib/libpng License | nsis.sourceforge.io |

Notes

* The ASIO-enabled PortAudio build that ships with `sounddevice` is **not** included in
  the installer (SSTV Station does not use ASIO).
* OpenSSL and SQLite are **not** included.
* Fonts (Segoe UI, Bahnschrift, Consolas) are not bundled; the program uses the fonts
  already present in Windows.

## Trademarks and protocols

* "Icom", "IC-7300" and "IC-7300MK2" are trademarks of Icom Inc. They are used only to describe
  compatibility. SSTV Station is not affiliated with, endorsed by or sponsored by Icom Inc.
* The CI-V commands used by SSTV Station are taken from Icom's publicly available CI-V
  reference documentation. Implementing a documented control protocol does not copy
  any Icom software.
* The SSTV modes (Martin, Scottie, Robot, PD, Wraase) are public signal formats. The encoder and
  decoder in `src/sstv.py` are an independent implementation from the published timing
  specifications; no code from MMSSTV, QSSTV, slowrx or other SSTV programs is used.
* The visual design is original. No third-party graphics, icons or logos are used; the
  program icon was created for this project.
