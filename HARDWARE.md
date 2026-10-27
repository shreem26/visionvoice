# VisionVoice — Hardware Build, Flash & Test Guide

Goal: build both boards **once**, prove every function with the test tool,
then fix everything in place so it never needs touching again. After that,
every improvement is a laptop software change (see `PROTOCOL.md`).

Allow about 2–3 hours if things go smoothly.

---

## 0. What you need

| Item | Used for |
|---|---|
| ESP32-CAM (AI-Thinker, OV2640) | Camera board |
| USB-to-TTL programmer (CP2102 / CH340 / FTDI) **or** ESP32-CAM-MB base | Flashing and powering the camera board |
| ESP32 DevKit (ESP32-WROOM, 30 or 38 pin) | Mic board |
| INMP441 I2S microphone | Voice input |
| 1 push button (optional) | Bigger push-to-talk button; the DevKit's BOOT button also works |
| Breadboard + jumper wires (F–F, M–F, M–M) | Connections |
| 2 USB **data** cables | Power + flashing |
| Phone hotspot set to **2.4 GHz** | Network for both boards + laptop |
| Laptop with Python 3.11+ | Runs everything else |

Software on the laptop:
1. **Arduino IDE 2.x**
2. Boards Manager → install **"esp32 by Espressif Systems"**, version **3.x**
   (this firmware was compile-tested with **3.3.8**; any 3.x should work, 2.x will NOT).
   If it's missing: *File → Preferences → Additional boards manager URLs* →
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
3. USB driver for your programmer/DevKit chip if Windows shows no COM port:
   CP210x (Silicon Labs) or CH340 (WCH).
4. The Python side: see `README.md` → Setup.

---

## 1. Mic board (ESP32 DevKit + INMP441)

### 1.1 Solder the INMP441 header (if its pins are loose)
Push the 6-pin header into the breadboard (long legs down), place the INMP441
on top, solder each pin 1–2 s (heat pad + pin, feed solder to the pin). Shiny
cones, no bridges. No iron? Any lab or phone-repair shop can do it in minutes.

### 1.2 Wiring (USB unplugged)

| INMP441 | → | ESP32 DevKit |
|---|---|---|
| VDD | → | **3V3** (never 5V/VIN) |
| GND | → | GND |
| L/R | → | GND |
| SCK | → | GPIO **32** (D32) |
| WS | → | GPIO **25** (D25) |
| SD | → | GPIO **33** (D33) |

| Push button (optional) | → | ESP32 DevKit |
|---|---|---|
| one leg | → | GPIO **27** (D27) |
| diagonal leg | → | GND |

```
 INMP441            ESP32 DevKit
 ┌──────┐
 │ VDD ─┼──────────── 3V3
 │ GND ─┼──────┬───── GND ──── button leg 2
 │ L/R ─┼──────┘
 │ SCK ─┼──────────── D32
 │ WS  ─┼──────────── D25
 │ SD  ─┼──────────── D33
 └──────┘             D27 ──── button leg 1
```
The mic's sound hole is the tiny hole on the module — keep it uncovered.

### 1.3 Flash
1. Open `firmware/vv_mic/vv_mic.ino` in Arduino IDE (the other files in the
   folder open as tabs — that's normal).
2. In the **config.h** tab, put your hotspot name and password in `WIFI_SSID_1`
   / `WIFI_PASS_1`.
3. *Tools → Board* → **ESP32 Dev Module**. *Tools → Port* → your COM port.
4. Upload. Stuck at `Connecting...`? Hold **BOOT** until it starts writing.
5. *Tools → Serial Monitor*, 115200 baud, press **EN/RST**. You should see:
   ```
   [boot] VisionVoice mic vv-mic-xxxxxx fw 1.0.0
   [i2s] ok (16 kHz, 32-bit stereo slots)
   [wifi] connected to <hotspot>, IP 192.168.x.x
   [ready] mic server on 192.168.x.x:5001
   ```
6. Type `level` + Enter while talking — the numbers should jump.
   Blue LED: fast blink = no Wi-Fi, slow blink = Wi-Fi OK, waiting for laptop.

## 2. Camera board (ESP32-CAM)

### 2.1 Check the board
Camera ribbon pushed in, black latch closed; peel the lens film.

### 2.2 Flash wiring
**ESP32-CAM-MB base:** just plug the camera in. Otherwise USB-to-TTL:

| Programmer | → | ESP32-CAM |
|---|---|---|
| GND | → | GND |
| 5V | → | 5V |
| TX | → | **U0R** |
| RX | → | **U0T** |
| — | | **IO0 → GND** (only while flashing) |

### 2.3 Flash
1. Open `firmware/vv_cam/vv_cam.ino`; fill in Wi-Fi in the **config.h** tab.
2. *Tools → Board* → **AI Thinker ESP32-CAM**; choose the port.
3. Upload; if it waits at `Connecting...`, press the **RST** button once.
4. **Remove the IO0→GND wire**, press RST, open Serial Monitor (115200):
   ```
   [boot] VisionVoice camera vv-cam-xxxxxx fw 1.0.0
   [cam] ok, PSRAM yes, max size UXGA
   [wifi] connected to <hotspot>, IP 192.168.x.x
   [ready] open http://192.168.x.x/ to aim the camera
   ```
5. Open that address on the laptop/phone: live view to aim the camera.
   Red LED on the back: fast blink = no Wi-Fi, blinking 2/s = camera error,
   tiny pulse every 3 s = all good.

| Problem | Fix |
|---|---|
| `Brownout detector was triggered` loop | Power from 5V (not 3.3V), better cable, no USB hub |
| `[cam] INIT FAILED 0x105/0x20001` | Unplug, reseat ribbon, latch down. It retries every 10 s by itself. |
| `PSRAM NO` | Board setting must be AI Thinker ESP32-CAM |
| Boots into "waiting for download" | IO0 is still connected to GND — remove it |

## 3. Network
- Laptop and both boards on the **same 2.4 GHz hotspot**.
- The laptop finds the boards automatically (UDP broadcast + `vv-cam.local` /
  `vv-mic.local`). If that ever fails, put the IPs from the Serial Monitor in
  `config.toml` under `[devices]`.
- Windows: if asked, allow Python on **Private** networks, and set the hotspot
  network profile to Private.

### Changing Wi-Fi later without reflashing
Connect the board by USB, open Serial Monitor (115200, "Newline"), type:
```
ssid=NewNetworkName
pass=NewPassword
save
```
It reboots and joins that network (config.h networks still work as backups).
`info` shows the current state, `clear` forgets the saved network.

## 4. Acceptance test (do this before fixing anything in place)

```
python tools/hwtest.py
```
It walks you through: Wi-Fi discovery → camera status → photos at two sizes →
flash → 10-photo stress test → reading a real book page (you type the page
number you placed) → mic connection → quiet vs. loud levels (also detects the
right channel) → push-to-talk recording → plays your voice back → speech
recognition → LED check. Report saved to `hwtest_output/report.txt`.

**All lines must be PASS (WARN only for optional items) before step 5.**
Typical fixes are all in `config.toml` — no reflashing:

| Report says | Change |
|---|---|
| recording too quiet | `[mic] gain_shift = 13` (or 12) |
| recording clipped | `[mic] gain_shift = 15` |
| sound is on channel R | `[mic] channel = "R"` |
| OCR text weak / page number wrong | light, distance 25–35 cm, page filling the frame; `[camera] rotate`, `controls = {vflip=1}` |

Then the long checks:
```
python tools/hwtest.py --reconnect      # unplug/replug both boards: they must come back by themselves
python tools/hwtest.py --soak 15        # 15 minutes: zero failed photos, zero mic disconnects
```

## 5. Make it permanent ("never touch again")
Do this only after the full test passes.
1. **Camera board:** remove the IO0 jumper for good. Leave the USB-to-TTL
   adapter attached as its power supply (it also gives you serial logs).
   Wrap the adapter–camera wires together with tape or a cable tie.
2. **Mount the camera** 25–35 cm above the table pointing straight down (phone
   stand / clamp / cardboard arm). Tape the base; mark the book position on the
   table with tape. Re-run `hwtest.py --only cam` after mounting.
3. **Mic board:** press every jumper fully home, then secure each end with a dab
   of hot glue or tape. Stick the breadboard (it has adhesive backing) to a
   piece of stiff cardboard/plastic. Keep the mic hole uncovered and pointing up.
   (Sturdier: solder everything onto a perfboard with the same wiring.)
4. **Strain relief:** tape both USB cables to the base so a tug can't pull a
   wire or the micro-USB connector.
5. **Label** both boards and cables (`CAM`, `MIC`). Photograph the finished
   wiring.
6. Power both boards from the laptop or one 2-port USB charger (≥2 A).
7. Run `python tools/hwtest.py --soak 15` one last time and keep the report.

## 6. Firmware facts (for your report)
- Camera firmware `1.0.0`: HTTP `/capture` `/status` `/control`, live view on
  port 81, discovery on UDP 4210, mDNS `vv-cam.local`, auto Wi-Fi reconnect,
  camera self-recovery. Photo settings are sent by the laptop on every request.
- Mic firmware `1.0.0`: I2S 16 kHz from INMP441 (both slots read, channel
  chosen by laptop), 2 s ring buffer with 250 ms pre-roll, streaming over TCP
  5001 while the button is held, laptop-controlled gain/LED/recording,
  heartbeats, auto Wi-Fi + client reconnect.
- Both compiled with arduino-esp32 3.3.8 (camera: 37 % flash, mic: 82 %).
