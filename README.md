# VisionVoice v1

Camera-based reading assistant: say **"read pages 12 to 14"**, hold the book
under the camera and say **"capture"** (or tap the button). VisionVoice reads a
page aloud **only if its printed page number is the one expected**. Otherwise
it tells you how many pages to turn and reads nothing. Ask **"summarize Mira"**
to hear only what the verified pages say about a character or object.

```
ESP32 DevKit + INMP441 ──TCP──┐                         ┌── speech (laptop speakers)
  (hold button & talk)        ├──► laptop: VisionVoice ─┤
ESP32-CAM ◄──HTTP /capture────┘   OCR · page check ·    └── captures/, visionvoice.log
                                  summary · speech-to-text
```

| Folder | What |
|---|---|
| `firmware/` | Final firmware for both boards (see `HARDWARE.md`) |
| `PROTOCOL.md` | The fixed hardware ↔ software contract |
| `visionvoice/` | The laptop app (Python) |
| `tools/hwtest.py` | Hardware acceptance test (PASS/FAIL report) |
| `tools/sim_devices.py` | Simulated boards: test everything without hardware |
| `tools/make_test_pages.py` | Generates test page photos (`sample_pages/`) |
| `tests/` | Automated tests (`python -m pytest`) |

## Setup (laptop)

1. **Python 3.11+**.
2. **Tesseract OCR**
   - Windows: install from the UB Mannheim build (github.com/UB-Mannheim/tesseract/wiki),
     then put its path in `config.toml` → `[ocr] tesseract_cmd = 'C:\Program Files\Tesseract-OCR\tesseract.exe'`
   - macOS: `brew install tesseract` · Ubuntu: `sudo apt install tesseract-ocr`
3. In this folder:
   ```
   python -m venv .venv
   .venv\Scripts\activate          (Windows)   |   source .venv/bin/activate   (Mac/Linux)
   pip install -r requirements.txt
   copy config.example.toml config.toml        (Windows)  |  cp config.example.toml config.toml
   ```
   The first voice command downloads the speech model (~150 MB, internet needed
   once). After that everything runs offline.

## Try it without hardware (2 minutes)

```
python -m pytest                                   # 61 tests, incl. full voice→capture→summary run
python -m visionvoice --no-devices                 # then type:
   read pages 12 to 13
   capture sample_pages/page_12.jpg
   capture sample_pages/page_15.jpg                # wrong page -> "go back 2 pages"
   capture sample_pages/page_13.jpg
   summarize the silver key
```
Simulated boards (two terminals):
```
python tools/sim_devices.py                        # terminal 1 (type a file name to choose the next photo, 'press' = button)
python tools/hwtest.py --auto                      # terminal 2
```

## Run with the real hardware
Build and test the boards with `HARDWARE.md` first, then:
```
python -m visionvoice
```
It finds both boards, says "VisionVoice ready", and then:

| Say (hold the button) / type | Does |
|---|---|
| read page 15 · read pages 12 to 15 | starts a reading session |
| capture · next · *(short tap on the button)* | photographs the page, checks the number, reads or warns |
| summarize Mira · explain the silver key · who is Oren | focused summary from verified pages only |
| repeat · stop · cancel · status · help · quit | — |
| *(typed only)* capture path/to/photo.jpg · devices | test with a file · re-find boards |

Pressing the button always interrupts speech. Every photo and voice clip is
kept in `captures/` (turn off with `[debug] save_captures = false`), and
decisions are logged in `visionvoice.log`.

## How the two features work
**Page check** (`vision/page_number.py`, `logic/session.py`): OCR word boxes →
only numbers in the top/bottom 12 % of the photo, isolated or at the end of a
short header line, not after "Chapter/Figure/…" → `DETECTED / MISSING /
AMBIGUOUS / UNREADABLE`. Only `DETECTED == expected` reads the page; a wrong
number gives "go back/forward N pages"; anything unclear asks for a recapture.
Pages are walked strictly in order.

**Summary** (`logic/summary.py`): sentences mentioning the name (also
possessives, plurals, OCR typos like "Mlra", the head noun of "magic ring", and
a following he/she/it sentence) from **verified pages only**; read back
verbatim with "Based on pages 12 to 13 only". `provider = "anthropic"` (with
`ANTHROPIC_API_KEY`) rewrites those sentences into a short summary, with a
check that rejects answers naming anything not in the evidence.

## Upgrading later (no hardware changes)
- New voice commands → `logic/commands.py`; behaviour → `app.py`.
- Web dashboard / history → add a Flask app that imports `App`; the hardware
  clients (`hw/camera.py`, `hw/mic.py`) are reusable as-is.
- Better speech recognition → `[stt] model = "small.en"` or a new engine in `audio/stt.py`.
- Always-listening mode → send `R1`/`R0` to the mic board (`MicClient.request_stream`).
- Camera tuning → `[camera]` in `config.toml`.

## Known limits (v1)
- Page numbers must be printed in the header/footer; a two-page spread is
  refused as ambiguous (show one page).
- Small or blurry print fails *closed* (asks to recapture) — light and distance matter.
- Summaries are only as good as the captured pages; nothing outside them is used.
- Speech recognition is tested with a stand-in in the automated tests; real
  recognition is checked by `hwtest.py` on your laptop.
