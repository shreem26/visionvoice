"""Simulated camera + mic boards that speak the exact PROTOCOL.md protocol.

Lets you test the laptop software (and hwtest.py) with no hardware:

  python tools/sim_devices.py --images sample_pages --wav some_command.wav --press-every 8

Camera: http://127.0.0.1:8080   Mic: 127.0.0.1:5001   Discovery: UDP 4210
Typing a page file name (e.g. page_13.jpg) + Enter selects the next photo the camera returns.
Typing 'press' + Enter simulates a button press that streams the WAV.
"""
from __future__ import annotations

import argparse
import io
import json
import socket
import struct
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

HDR = struct.Struct("<BcI")


def frame(t: str, payload: bytes | str = b"") -> bytes:
    if isinstance(payload, str):
        payload = payload.encode()
    return HDR.pack(0x56, t.encode(), len(payload)) + payload


# ---------------- camera ----------------
class CamState:
    def __init__(self, images: list[Path]):
        self.images = images
        self.index = 0
        self.captures = 0
        self.controls: dict[str, int] = {}
        self.lock = threading.Lock()

    def next_image(self) -> bytes:
        with self.lock:
            if not self.images:
                return _blank_jpeg()
            p = self.images[self.index % len(self.images)]
            self.captures += 1
            return p.read_bytes()

    def select(self, name: str) -> bool:
        for i, p in enumerate(self.images):
            if p.name == name:
                self.index = i
                return True
        return False


def _blank_jpeg() -> bytes:
    from PIL import Image

    b = io.BytesIO()
    Image.new("RGB", (1600, 1200), (240, 240, 240)).save(b, "JPEG")
    return b.getvalue()


def make_cam_handler(state: CamState, port: int):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/status":
                self._json({"vv": 1, "role": "cam", "id": "vv-cam-sim", "fw": "1.0.0-sim", "ip": "127.0.0.1",
                            "rssi": -50, "uptime_s": int(time.time() % 100000), "heap": 100000, "psram": 4194304,
                            "camera_ok": True, "camera_err": 0, "size": q.get("size", "UXGA"), "quality": 10,
                            "captures": state.captures, "failures": 0})
            elif u.path == "/capture":
                data = state.next_image()
                from PIL import Image

                w, h = Image.open(io.BytesIO(data)).size
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("X-VV-Resolution", f"{w}x{h}")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif u.path == "/control":
                try:
                    state.controls[q["var"]] = int(q["val"])
                    self._json({"ok": True})
                except (KeyError, ValueError):
                    self._json({"ok": False, "error": "need var and val"}, 400)
            else:
                self._json({"error": "not found"}, 404)

    return H


# ---------------- mic ----------------
class SimMic:
    def __init__(self, port: int, wav: Path | None, realtime: bool = True):
        self.port = port
        self.pcm = _load_pcm(wav) if wav else _tone()
        self.realtime = realtime
        self.client: socket.socket | None = None
        self.lock = threading.Lock()
        self.cfg = {"gain_shift": 14, "channel": "L", "max_ms": 15000, "preroll_ms": 250, "hpf": 1}
        self.led = 4
        self.log: list[str] = []

    def send(self, t, p=b""):
        with self.lock:
            if self.client:
                try:
                    self.client.sendall(frame(t, p))
                except OSError:
                    self.client = None

    def status(self):
        self.send("S", json.dumps({"up": int(time.time() % 100000), "rssi": -48, "heap": 180000, "btn": 0, "stream": 0,
                                   "lvl": [int(np.random.randint(200, 3000)), int(np.random.randint(0, 40))],
                                   "drops": 0, "cfg": self.cfg}))

    def serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(1)
        while True:
            c, addr = srv.accept()
            with self.lock:
                if self.client:
                    try:
                        self.client.close()
                    except OSError:
                        pass
                self.client = c
            self.send("H", json.dumps({"vv": 1, "role": "mic", "id": "vv-mic-sim", "fw": "1.0.0-sim", "rate": 16000,
                                       "bits": 16, "channels": 1}))
            self.status()
            threading.Thread(target=self._reader, args=(c,), daemon=True).start()

    def _reader(self, c: socket.socket):
        buf = b""
        while True:
            try:
                d = c.recv(4096)
            except OSError:
                return
            if not d:
                return
            buf += d
            while len(buf) >= 6:
                _, t, n = HDR.unpack_from(buf)
                if len(buf) < 6 + n:
                    break
                p, buf = buf[6:6 + n], buf[6 + n:]
                t = t.decode()
                self.log.append(t)
                if t == "P":
                    self.status()
                elif t == "C":
                    for kv in p.decode().split(";"):
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            self.cfg[k] = v
                    self.status()
                elif t == "I" and p:
                    self.led = p[0]
                elif t == "R":
                    if p == b"1":
                        threading.Thread(target=self.press, args=(None, "host"), daemon=True).start()

    def status_loop(self):
        while True:
            time.sleep(1)
            self.status()

    def press(self, held_ms: int | None = None, reason: str = "button"):
        pcm = self.pcm
        if held_ms is None:
            held_ms = int(len(pcm) / 2 / 16)
        else:
            pcm = pcm[: held_ms * 32]
        if reason == "button":
            self.send("D", json.dumps({"src": "boot", "ms": int(time.time() * 1000) % 10**9}))
        self.send("B", json.dumps({"reason": reason, "preroll_ms": 250}))
        chunk = 1024
        for i in range(0, len(pcm), chunk):
            self.send("A", pcm[i:i + chunk])
            if self.realtime:
                time.sleep(0.032)
        if reason == "button":
            self.send("U", json.dumps({"ms": 0, "held_ms": held_ms}))
        self.send("E", json.dumps({"reason": reason, "samples": len(pcm) // 2, "drops": 0}))


def _load_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2 and w.getframerate() == 16000, "WAV must be 16 kHz 16-bit"
        data = w.readframes(w.getnframes())
        if w.getnchannels() == 2:
            data = np.frombuffer(data, "<i2").reshape(-1, 2)[:, 0].tobytes()
        return data


def _tone(seconds: float = 1.5) -> bytes:
    t = np.arange(int(16000 * seconds)) / 16000
    return (np.sin(2 * np.pi * 440 * t) * 6000).astype("<i2").tobytes()


# ---------------- discovery ----------------
def discovery(cam_port: int, mic_port: int, udp_port: int = 4210):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", udp_port))
    while True:
        data, addr = s.recvfrom(256)
        if data.startswith(b"VV?DISCOVER"):
            s.sendto(json.dumps({"vv": 1, "role": "cam", "id": "vv-cam-sim", "fw": "1.0.0-sim", "ip": "127.0.0.1",
                                 "port": cam_port, "stream_port": 0}).encode(), addr)
            s.sendto(json.dumps({"vv": 1, "role": "mic", "id": "vv-mic-sim", "fw": "1.0.0-sim", "ip": "127.0.0.1",
                                 "port": mic_port}).encode(), addr)


def start(images_dir: str | None, wav: str | None, cam_port=8080, mic_port=5001, udp_port=4210, realtime=True):
    imgs = sorted(Path(images_dir).glob("*.jpg")) if images_dir else []
    cam = CamState(imgs)
    httpd = ThreadingHTTPServer(("0.0.0.0", cam_port), make_cam_handler(cam, cam_port))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    mic = SimMic(mic_port, Path(wav) if wav else None, realtime)
    threading.Thread(target=mic.serve, daemon=True).start()
    threading.Thread(target=mic.status_loop, daemon=True).start()
    if udp_port:
        threading.Thread(target=discovery, args=(cam_port, mic_port, udp_port), daemon=True).start()
    return cam, mic, httpd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default="sample_pages")
    ap.add_argument("--wav")
    ap.add_argument("--cam-port", type=int, default=8080)
    ap.add_argument("--mic-port", type=int, default=5001)
    ap.add_argument("--press-every", type=float, default=0)
    a = ap.parse_args()
    cam, mic, _ = start(a.images, a.wav, a.cam_port, a.mic_port)
    print(f"sim camera http://127.0.0.1:{a.cam_port}  sim mic 127.0.0.1:{a.mic_port}  images={len(cam.images)}")
    if a.press_every:
        def presser():
            while True:
                time.sleep(a.press_every)
                mic.press()
        threading.Thread(target=presser, daemon=True).start()
    while True:
        line = input().strip()
        if line == "press":
            mic.press()
        elif line.startswith("tap"):
            mic.press(150)
        elif cam.select(line):
            print("next photo:", line)
        else:
            print("commands: press | tap | <image file name>")


if __name__ == "__main__":
    main()
