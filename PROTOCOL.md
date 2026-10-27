# VisionVoice Hardware Protocol v1 (frozen)

This is the contract between the two ESP32 boards and the laptop software.
**The firmware only moves data; every decision is made on the laptop.**
Behaviour changes (new commands, new gestures, different camera settings,
mic gain, always-listening mode…) are software changes, so the firmware
never has to be reflashed.

Rule for future versions: only *add* new message types/parameters. Never
change the meaning of existing ones. Unknown message types are ignored on
both sides.

---

## 1. Discovery (both boards)

- Each board listens on **UDP port 4210**.
- Laptop sends the ASCII text `VV?DISCOVER` (broadcast to `255.255.255.255:4210`
  or unicast to a known IP).
- Each board replies to the sender's IP/port with one JSON line:

```json
{"vv":1,"role":"cam","id":"vv-cam-1a2b3c","fw":"1.0.0","ip":"192.168.43.50","port":80,"stream_port":81}
{"vv":1,"role":"mic","id":"vv-mic-4d5e6f","fw":"1.0.0","ip":"192.168.43.51","port":5001}
```

- Boards also register mDNS names `vv-cam.local` / `vv-mic.local`.
- The laptop always starts the connections, so the laptop needs no fixed IP
  and no inbound firewall rule, and the firmware never needs the laptop's address.

## 2. Camera board (ESP32-CAM, HTTP)

| Request | Response |
|---|---|
| `GET /status` | JSON: `vv, role, id, fw, ip, rssi, uptime_s, heap, psram, camera_ok, camera_err, size, quality, captures, failures` |
| `GET /capture?size=UXGA&quality=10&flash=0&skip=1` | `image/jpeg`. Header `X-VV-Resolution: 1600x1200`. All params optional. |
| `GET /control?var=<name>&val=<int>` | JSON `{"ok":true}` or `{"ok":false,"error":"..."}` |
| `GET /` | Small web page with live view (for aiming the camera) |
| `GET :81/stream` | MJPEG live stream (aiming only) |
| `GET /reboot` | Reboots |

- `size`: `QVGA, VGA, SVGA, XGA, HD, SXGA, UXGA`
- `quality`: 8–63 (lower = better JPEG, bigger file)
- `flash`: 0–255 flash-LED brightness during capture (0 = off)
- `skip`: frames to throw away first, so the photo is fresh (0–5)
- `/control` vars: `brightness contrast saturation ae_level` (−2…2),
  `vflip hmirror awb aec aec2 agc dcw lenc` (0/1), `wb_mode` (0–4),
  `aec_value` (0–1200), `gainceiling` (0–6)
- Settings are **not** saved on the board; the laptop sends its settings each
  time it connects.

## 3. Microphone board (ESP32 DevKit + INMP441, TCP port 5001)

One TCP connection at a time (a new connection replaces the old one).

### Frame format (both directions)

```
byte 0     : 0x56 ('V')  magic
byte 1     : type (ASCII letter)
bytes 2..5 : payload length, uint32 little-endian
bytes 6..  : payload
```

### Board → laptop

| Type | Payload | When |
|---|---|---|
| `H` | JSON `{"vv":1,"role":"mic","id","fw","rate":16000,"bits":16,"channels":1}` | right after connect |
| `S` | JSON `{"up":s,"rssi":dBm,"heap":n,"btn":0/1,"stream":0/1,"lvl":[peakL,peakR],"drops":n,"cfg":{...}}` | every 1 s, and in reply to `P` |
| `D` | JSON `{"src":"boot"\|"ext","ms":uptime_ms}` | button pressed |
| `U` | JSON `{"ms":uptime_ms,"held_ms":n}` | button released |
| `B` | JSON `{"reason":"button"\|"host","preroll_ms":n}` | audio stream begins |
| `A` | raw PCM, int16 little-endian, mono, 16 kHz | while streaming (~32 ms per frame) |
| `E` | JSON `{"reason":"button"\|"host"\|"max","samples":n,"drops":n}` | audio stream ends |
| `L` | text | log line |

`lvl` = peak of left/right I2S slots since the last status (0–32767). Used by
the test tool to check wiring and channel without streaming.

### Laptop → board

| Type | Payload | Effect |
|---|---|---|
| `P` | empty | ping; board replies with `S`. Laptop pings every 2 s. Board drops a client that sends nothing for 10 s. |
| `C` | text `key=value;key=value` | config: `gain_shift` (8–20, default 14), `channel` (`L`/`R`, default L), `max_ms` (500–30000, default 15000), `preroll_ms` (0–500, default 250), `hpf` (0/1, default 1) |
| `R` | `1` / `0` | start / stop streaming on request (e.g. a future always-listening mode) |
| `I` | 1 byte | LED mode when idle: 0 off, 1 on, 2 slow blink, 3 fast blink, 4 short pulse (default) |
| `X` | empty | reboot |

### Board behaviour (fixed)

- Button (BOOT GPIO0 **or** external GPIO27) pressed → `D`, then stream starts
  (`B` + pre-roll audio + live `A` frames). Released → `U`, stream stops (`E`).
- The stream also stops at `max_ms`.
- LED: solid while streaming; fast blink = no Wi-Fi; slow blink = Wi-Fi OK but
  no laptop connected; otherwise the laptop's `I` mode.
- Meaning of a press (talk vs. short "tap" gesture, etc.) is decided on the laptop.
