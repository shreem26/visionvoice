"""Mic board client (PROTOCOL.md section 3).

Runs a background thread that keeps a TCP connection to the board, reconnects
automatically, and turns frames into events on a queue.
"""
from __future__ import annotations

import json
import queue
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .protocol import FrameReader, encode_frame

SAMPLE_RATE = 16000


@dataclass
class MicEvent:
    kind: str  # connected | disconnected | status | button_down | button_up | utterance | log
    data: dict = field(default_factory=dict)
    pcm: bytes = b""  # int16 LE mono 16 kHz (utterance only)


class MicClient:
    LED_OFF, LED_ON, LED_SLOW, LED_FAST, LED_PULSE = 0, 1, 2, 3, 4

    def __init__(
        self,
        host: str,
        port: int = 5001,
        config: dict | None = None,
        on_button_down: Callable[[], None] | None = None,
    ) -> None:
        self.host, self.port = host, port
        self.config = config or {}
        self.events: "queue.Queue[MicEvent]" = queue.Queue()
        self.on_button_down = on_button_down
        self.connected = threading.Event()
        self.hello: dict = {}
        self.last_status: dict = {}
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mic-client", daemon=True)
        self._audio = bytearray()
        self._stream_info: dict = {}
        self._held_ms = 0

    # ---- public API ----
    def start(self) -> "MicClient":
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        self._close_sock()

    def wait_connected(self, timeout: float) -> bool:
        return self.connected.wait(timeout)

    def send(self, ftype: str, payload: bytes | str = b"") -> bool:
        sock = self._sock
        if not sock:
            return False
        try:
            with self._send_lock:
                sock.sendall(encode_frame(ftype, payload))
            return True
        except OSError:
            self._close_sock()
            return False

    def set_led(self, mode: int) -> bool:
        return self.send("I", bytes([mode]))

    def set_config(self, **kw) -> bool:
        return self.send("C", ";".join(f"{k}={v}" for k, v in kw.items()))

    def request_stream(self, on: bool) -> bool:
        return self.send("R", b"1" if on else b"0")

    def ping(self) -> bool:
        return self.send("P")

    def reboot(self) -> bool:
        return self.send("X")

    # ---- internals ----
    def _close_sock(self) -> None:
        s, self._sock = self._sock, None
        if s:
            try:
                s.close()
            except OSError:
                pass

    def _apply_config(self) -> None:
        keys = ("gain_shift", "channel", "max_ms", "preroll_ms", "hpf")
        cfg = {k: self.config[k] for k in keys if k in self.config}
        if cfg:
            self.set_config(**cfg)
        self.set_led(self.LED_PULSE)

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                sock = socket.create_connection((self.host, self.port), timeout=5)
            except OSError:
                time.sleep(backoff)
                backoff = min(backoff * 1.5, 5.0)
                continue
            backoff = 1.0
            sock.settimeout(0.5)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock = sock
            reader = FrameReader()
            last_rx = time.time()
            last_ping = 0.0
            got_hello = False
            try:
                while not self._stop.is_set() and self._sock is sock:
                    now = time.time()
                    if now - last_ping > 2.0:
                        self.ping()
                        last_ping = now
                    if now - last_rx > 5.0:
                        raise OSError("mic board stopped responding")
                    try:
                        data = sock.recv(8192)
                    except socket.timeout:
                        continue
                    if not data:
                        raise OSError("connection closed by mic board")
                    last_rx = time.time()
                    for ftype, payload in reader.feed(data):
                        if ftype == "H" and not got_hello:
                            got_hello = True
                            self.hello = _json(payload)
                            self._apply_config()
                            self.connected.set()
                            self.events.put(MicEvent("connected", self.hello))
                        self._handle(ftype, payload)
            except OSError as e:
                if self.connected.is_set():
                    self.events.put(MicEvent("disconnected", {"reason": str(e)}))
                self.connected.clear()
                self._close_sock()
                time.sleep(1.0)

    def _handle(self, ftype: str, payload: bytes) -> None:
        if ftype == "S":
            self.last_status = _json(payload)
            self.events.put(MicEvent("status", self.last_status))
        elif ftype == "D":
            if self.on_button_down:
                try:
                    self.on_button_down()
                except Exception:
                    pass
            self.events.put(MicEvent("button_down", _json(payload)))
        elif ftype == "U":
            info = _json(payload)
            self._held_ms = int(info.get("held_ms", 0))
            self.events.put(MicEvent("button_up", info))
        elif ftype == "B":
            self._audio = bytearray()
            self._stream_info = _json(payload)
            self._held_ms = 0
        elif ftype == "A":
            self._audio.extend(payload)
        elif ftype == "E":
            info = _json(payload)
            info.update(self._stream_info)
            info["reason"] = _json(payload).get("reason", "")
            info["held_ms"] = self._held_ms
            info["seconds"] = len(self._audio) / 2 / SAMPLE_RATE
            self.events.put(MicEvent("utterance", info, bytes(self._audio)))
            self._audio = bytearray()
        elif ftype == "L":
            self.events.put(MicEvent("log", {"text": payload.decode(errors="replace")}))


def _json(payload: bytes) -> dict:
    try:
        v = json.loads(payload.decode() or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}
