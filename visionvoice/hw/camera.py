"""Camera board client (PROTOCOL.md section 2) and a file-based stand-in."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import requests


class CameraError(Exception):
    pass


@dataclass
class Photo:
    jpeg: bytes
    resolution: str
    seconds: float
    source: str


class CameraClient:
    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        if not base_url.startswith("http"):
            base_url = "http://" + base_url
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()

    def status(self) -> dict:
        try:
            r = self.http.get(self.base_url + "/status", timeout=5)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            raise CameraError(f"camera not reachable at {self.base_url}: {e}") from e

    def control(self, var: str, val: int) -> bool:
        try:
            r = self.http.get(self.base_url + "/control", params={"var": var, "val": int(val)}, timeout=5)
            return r.ok and r.json().get("ok", False)
        except (requests.RequestException, ValueError):
            return False

    def apply_controls(self, controls: dict) -> dict[str, bool]:
        return {k: self.control(k, v) for k, v in (controls or {}).items()}

    def capture(self, size: str = "UXGA", quality: int = 10, flash: int = 0, skip: int = 1, retries: int = 1) -> Photo:
        params = {"size": size, "quality": quality, "flash": flash, "skip": skip}
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            t0 = time.time()
            try:
                r = self.http.get(self.base_url + "/capture", params=params, timeout=self.timeout)
                if r.status_code != 200:
                    raise CameraError(f"camera returned HTTP {r.status_code}: {r.text[:120]}")
                if not r.content.startswith(b"\xff\xd8"):
                    raise CameraError("camera did not return a JPEG")
                return Photo(r.content, r.headers.get("X-VV-Resolution", "?"), time.time() - t0, "esp32-cam")
            except (requests.RequestException, CameraError) as e:
                last_err = e
                time.sleep(0.5)
        raise CameraError(f"capture failed: {last_err}")


class FileCamera:
    """Uses an image file instead of the camera (for testing the software)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def capture(self, **_: object) -> Photo:
        data = self.path.read_bytes()
        return Photo(data, "file", 0.0, f"file:{self.path.name}")
