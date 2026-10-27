"""Configuration: defaults + optional config.toml next to where you run the app."""
from __future__ import annotations

import copy
import os
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

DEFAULTS: dict = {
    "devices": {
        "cam": "",        # "" = auto-discover, or "http://192.168.43.50"
        "mic": "",        # "" = auto-discover, or "192.168.43.51:5001"
        "discovery_timeout": 2.0,
    },
    "camera": {
        "size": "UXGA",
        "quality": 10,
        "flash": 0,       # 0-255 flash LED brightness during capture
        "skip": 1,
        "rotate": 0,      # degrees clockwise to rotate photos on the laptop (0/90/180/270)
        "timeout": 15.0,
        "controls": {},   # e.g. {vflip = 1, hmirror = 0, brightness = 1}
    },
    "mic": {
        "gain_shift": 14,  # lower = louder (8-20)
        "channel": "L",
        "max_ms": 15000,
        "preroll_ms": 250,
        "hpf": 1,
        "tap_ms": 400,     # presses shorter than this (with no speech) mean "capture"
    },
    "ocr": {
        "tesseract_cmd": "",   # Windows: r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
        "lang": "eng",
        "band": 0.12,          # top/bottom fraction of the photo searched for the page number
        "min_conf": 60,
    },
    "session": {"max_range_pages": 30},
    "stt": {"engine": "faster_whisper", "model": "base.en", "device": "cpu", "compute_type": "int8"},
    "summary": {"provider": "extractive", "model": "claude-haiku-5-5", "max_words": 60},
    "tts": {"engine": "auto", "rate": 0},
    "debug": {"save_captures": True, "captures_dir": "captures", "log_file": "visionvoice.log"},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | os.PathLike | None = None) -> dict:
    """Load config.toml (if present) on top of the defaults."""
    p = Path(path) if path else Path("config.toml")
    if p.exists():
        if tomllib is None:
            raise RuntimeError("config.toml needs Python 3.11 or newer")
        with open(p, "rb") as f:
            return _merge(DEFAULTS, tomllib.load(f))
    return copy.deepcopy(DEFAULTS)
