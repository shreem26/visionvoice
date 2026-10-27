"""Image decoding, light preprocessing and Tesseract OCR."""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
import pytesseract


class OcrError(Exception):
    pass


@dataclass
class Word:
    text: str
    conf: float          # 0-100
    box: tuple[int, int, int, int]  # x, y, w, h in the processed image
    line_key: tuple[int, int, int]  # (block, paragraph, line)


@dataclass
class OcrResult:
    text: str
    words: list[Word]
    mean_conf: float
    width: int
    height: int
    gray: np.ndarray = field(repr=False)


def setup_tesseract(cmd: str = "") -> str:
    """Point pytesseract at the binary and return its version (raises OcrError if missing)."""
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        return str(pytesseract.get_tesseract_version())
    except Exception as e:  # TesseractNotFoundError and friends
        raise OcrError(
            "Tesseract OCR is not installed or not found. Install it and/or set ocr.tesseract_cmd in config.toml."
        ) from e


def decode_image(data: bytes, rotate: int = 0) -> np.ndarray:
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise OcrError("The photo could not be decoded.")
    rot = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(int(rotate) % 360)
    if rot is not None:
        img = cv2.rotate(img, rot)
    return img


def preprocess(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape
    if max(h, w) < 2000:  # small camera photos: upscale so letters are ~20-30 px tall
        scale = 2000 / max(h, w)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    # even out lighting (flash hot-spot / shadows) without binarising too early
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def words_from_data(d: dict, offset: tuple[int, int] = (0, 0), scale: float = 1.0) -> list[Word]:
    out = []
    for i, txt in enumerate(d["text"]):
        txt = (txt or "").strip()
        try:
            conf = float(d["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if not txt or conf < 0:
            continue
        x = int(d["left"][i] / scale) + offset[0]
        y = int(d["top"][i] / scale) + offset[1]
        w = int(d["width"][i] / scale)
        h = int(d["height"][i] / scale)
        out.append(Word(txt, conf, (x, y, w, h), (d["block_num"][i], d["par_num"][i], d["line_num"][i])))
    return out


def readable_text(text: str) -> str:
    """Text for reading aloud: drop lone page-number lines, re-join wrapped lines and hyphenated words."""
    import re

    lines = [ln for ln in text.splitlines() if not re.fullmatch(r"\W*\d{1,4}\W*", ln.strip())]
    t = "\n".join(lines)
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    paras = [re.sub(r"\s*\n\s*", " ", p).strip() for p in re.split(r"\n\s*\n", t)]
    return "\n\n".join(p for p in paras if p)


def run_ocr(img: np.ndarray, lang: str = "eng") -> OcrResult:
    gray = preprocess(img)
    try:
        d = pytesseract.image_to_data(gray, lang=lang, config="--psm 3", output_type=pytesseract.Output.DICT)
    except Exception as e:
        raise OcrError(f"OCR failed: {e}") from e
    words = words_from_data(d)
    # rebuild readable text: lines in reading order, blank line between paragraphs
    lines: dict[tuple, list[Word]] = {}
    for w in words:
        lines.setdefault(w.line_key, []).append(w)
    parts: list[str] = []
    prev_par = None
    for key in sorted(lines):
        par = key[:2]
        if prev_par is not None and par != prev_par:
            parts.append("")
        parts.append(" ".join(w.text for w in lines[key]))
        prev_par = par
    text = "\n".join(parts).strip()
    mean = float(np.mean([w.conf for w in words])) if words else 0.0
    h, w = gray.shape
    return OcrResult(text, words, mean, w, h, gray)
