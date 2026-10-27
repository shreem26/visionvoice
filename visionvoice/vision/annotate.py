"""Draw what the page check saw: header/footer search bands and page-number candidates."""
from __future__ import annotations

import cv2
import numpy as np

from .ocr import OcrResult
from .page_number import PageNumberResult

GREEN = (60, 170, 60)
ORANGE = (0, 140, 255)
BLUE = (200, 120, 40)


def annotate(ocr: OcrResult, det: PageNumberResult, band: float, width: int = 900) -> bytes:
    img = cv2.cvtColor(ocr.gray, cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    bh = int(h * band)
    overlay = img.copy()
    cv2.rectangle(overlay, (0, 0), (w, bh), BLUE, -1)
    cv2.rectangle(overlay, (0, h - bh), (w, h), BLUE, -1)
    img = cv2.addWeighted(overlay, 0.12, img, 0.88, 0)
    for y in (bh, h - bh):
        cv2.line(img, (0, y), (w, y), BLUE, max(2, w // 600), cv2.LINE_AA)
    chosen = det.page if det.status == "DETECTED" else None
    thick = max(3, w // 300)
    for c in det.candidates:
        if not c.box:
            continue
        x, y, bw, bhh = c.box
        color = GREEN if c.value == chosen else ORANGE
        pad = max(8, bhh // 3)
        cv2.rectangle(img, (x - pad, y - pad), (x + bw + pad, y + bhh + pad), color, thick)
        label = f"{c.value}  {int(c.conf * 100)}%"
        scale = max(0.8, w / 1400)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        ty = y - pad - 10 if y - pad - th - 14 > 0 else y + bhh + pad + th + 10
        cv2.rectangle(img, (x - pad, ty - th - 8), (x - pad + tw + 12, ty + 6), color, -1)
        cv2.putText(img, label, (x - pad + 6, ty), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2, cv2.LINE_AA)
    if w > width:
        img = cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes() if ok else b""


def thumbnail(jpeg: bytes, rotate_img: np.ndarray | None = None, width: int = 900) -> bytes:
    img = rotate_img if rotate_img is not None else cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return b""
    h, w = img.shape[:2]
    if w > width:
        img = cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes() if ok else b""
