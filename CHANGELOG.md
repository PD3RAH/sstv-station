# Changelog

## 1.0.1 – 2026-11-12
- Auto-ALC (bottom bar, on by default): lowers the TX level automatically while transmitting until the
  ALC meter is back in the green zone; the level found is kept for the next transmission
- The TX level slider now works live, also in the middle of a transmission

## 1.0.0 – 2026-11-10
- First release, in the same layout as CW Station
- Receive and transmit 15 SSTV modes: Martin M1/M2, Scottie S1/S2/DX, Robot 36/72,
  PD 50/90/120/160/180/240/290, Wraase SC2-180
- VIS detection with tuning-offset correction; free-run start on sync pulses without VIS
- Automatic slant correction (linear fit of the sync pulses), picture rebuilt during and after reception
- Gallery with automatic PNG saving; save as, use received picture for TX
- Transmit via CI-V PTT and the USB audio CODEC; USB-D/LSB-D selected automatically; progress and STOP (Esc)
- Picture composer: photo cropped to the mode size, text templates F1–F8, inset of the received picture
- Band buttons on the SSTV calling frequencies, SSTV tools panel (decoder status, mode table, frequencies)
- Spectrum scope, radio control, S-meter and TX meters, logbook with ADIF export (mode SSTV, RSV)
- Automatic start: connects to the radio and starts the audio at start-up, retries every 10 s until the
  radio is on; devices are found again by name when their number changes
- One-click update: downloads SSTVStation-Update from the GitHub release, installs silently and restarts
- SSTV signals are recognised in the spectrum scope and framed in green with their dial frequency; click to tune
- Scope click tunes so the clicked signal lands at 1750 Hz audio (USB/LSB aware); decoding mode shown in the audio waterfall
- Band buttons widen the IF filter to 2.7 kHz (IC-7300 SSB-D default FIL1 is 1.2 kHz)
- VIS accepted with up to ±250 Hz tuning offset; sync threshold follows the offset
- Better weak-signal reception: narrow channel filter around 1750 Hz, amplitude-weighted frequency per pixel,
  VIS detection corrected for noise (works down to about 1–3 dB SNR), keeps decoding through short fades
- Receive filter Sharp / Normal / Low noise and a denoise filter (3×3 median), both in the Decoder tab
- Decoder tab: measured tone, sync pulses per second and a hint why nothing decodes
- ● REC (record RX audio as WAV) and WAV… (decode a WAV file)
- Short, readable audio device list (each device once, USB Audio CODEC first); Audio button green when on
- Loopback test for all modes (noise, clock error, tuning offset, 44.1 kHz, without VIS)
