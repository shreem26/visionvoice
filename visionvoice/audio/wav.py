"""WAV helpers for 16 kHz mono int16 audio from the mic board."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

RATE = 16000


def save_wav(path: str | Path, pcm: bytes, rate: int = RATE) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return p


def load_wav(path: str | Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("need 16-bit WAV")
        data = w.readframes(w.getnframes())
        if w.getnchannels() == 2:
            a = np.frombuffer(data, dtype="<i2").reshape(-1, 2).mean(axis=1).astype("<i2")
            data = a.tobytes()
        return data, w.getframerate()


def to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def stats(pcm: bytes, rate: int = RATE) -> dict:
    a = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    if a.size == 0:
        return {"seconds": 0.0, "rms": 0.0, "peak": 0, "clipped": 0.0}
    return {
        "seconds": a.size / rate,
        "rms": float(np.sqrt(np.mean(a * a))),
        "peak": int(np.max(np.abs(a))),
        "clipped": float(np.mean(np.abs(a) >= 32700)),
    }
