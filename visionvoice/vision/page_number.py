"""Find the PRINTED page number (header/footer only) and check it against the expected page.

Fails closed: anything unclear is UNCERTAIN and the page is not read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import cv2
import pytesseract

from .ocr import OcrResult, Word, words_from_data

NUM_RE = re.compile(r"^[\W_]*(\d{1,4})[\W_]*$")
BLOCKERS = {"chapter", "chap", "part", "section", "vol", "volume", "book", "figure", "fig", "table", "no", "act", "scene"}
MAX_HEADER_WORDS = 7


@dataclass
class Candidate:
    value: int
    region: str
    conf: float      # 0-1 OCR confidence
    score: float     # conf x placement weight
    isolated: bool


@dataclass
class PageNumberResult:
    status: str                    # DETECTED | MISSING | AMBIGUOUS | UNREADABLE
    page: int | None = None
    confidence: float = 0.0
    region: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _candidates(words: list[Word], width: int, height: int, band: float) -> list[Candidate]:
    lines: dict[tuple, list[Word]] = {}
    for w in words:
        lines.setdefault(w.line_key, []).append(w)
    for ws in lines.values():
        ws.sort(key=lambda w: w.box[0])
    out = []
    for key, ws in lines.items():
        for idx, w in enumerate(ws):
            m = NUM_RE.match(w.text)
            if not m:
                continue
            x, y, bw, bh = w.box
            cy = y + bh / 2
            if cy < band * height:
                vert = "top"
            elif cy > (1 - band) * height:
                vert = "bottom"
            else:
                continue  # body text numbers never count
            cx = x + bw / 2
            horiz = "left" if cx < width / 3 else ("right" if cx > 2 * width / 3 else "center")
            others = [o for o in ws if o is not w and re.search(r"[A-Za-z]", o.text)]
            prev = ws[idx - 1].text.lower().strip(".:") if idx > 0 else ""
            if prev in BLOCKERS:
                continue  # "Chapter 3", "Figure 2"
            isolated = not others or (prev == "page" and len(ws) <= 3)
            if not isolated:
                # allow running headers like "14   THE GLASS SLIPPER" (number at either end of a short line)
                if idx not in (0, len(ws) - 1) or len(ws) > MAX_HEADER_WORDS:
                    continue
            value = int(m.group(1))
            if value < 1:
                continue
            conf = max(0.0, min(1.0, w.conf / 100))
            edge = 1 - (cy / height if vert == "top" else (height - cy) / height) / band  # 1 at the edge
            weight = (1.0 if isolated else 0.6) * (0.85 + 0.15 * max(0.0, edge))
            out.append(Candidate(value, f"{vert}_{horiz}", conf, conf * weight, isolated))
    return out


def _band_pass(ocr: OcrResult, band: float, lang: str) -> list[Word]:
    """Second OCR pass on just the header/footer strips, enlarged, to catch small page numbers."""
    h, w = ocr.gray.shape
    bh = int(h * band)
    words: list[Word] = []
    for y0 in (0, h - bh):
        crop = ocr.gray[y0 : y0 + bh, :]
        big = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        try:
            d = pytesseract.image_to_data(big, lang=lang, config="--psm 11", output_type=pytesseract.Output.DICT)
        except Exception:
            continue
        # psm 11 has no reliable lines; give each word its own line so isolation is judged on neighbours below
        ws = words_from_data(d, offset=(0, y0), scale=2.0)
        # rebuild lines by vertical overlap so "Chapter 3" style headers are still recognised
        ws.sort(key=lambda w: (w.box[1], w.box[0]))
        line_id = 0
        last_mid = None
        for wd in ws:
            mid = wd.box[1] + wd.box[3] / 2
            if last_mid is None or abs(mid - last_mid) > max(8, wd.box[3] * 0.6):
                line_id += 1
                last_mid = mid
            wd.line_key = (9000 + y0, 0, line_id)
        words.extend(ws)
    return words


def detect_page_number(ocr: OcrResult, band: float = 0.12, min_conf: float = 60, lang: str = "eng") -> PageNumberResult:
    cands = _candidates(ocr.words, ocr.width, ocr.height, band)
    warnings: list[str] = []
    if not any(c.conf * 100 >= min_conf for c in cands):
        extra = _candidates(_band_pass(ocr, band, lang), ocr.width, ocr.height, band)
        if extra:
            warnings.append("page number found by the enlarged header/footer pass")
        cands += extra
    if not cands:
        return PageNumberResult("MISSING", warnings=warnings)

    best: dict[int, Candidate] = {}
    for c in cands:
        if c.value not in best or c.score > best[c.value].score:
            best[c.value] = c
    ranked = sorted(best.values(), key=lambda c: c.score, reverse=True)
    strong = [c for c in ranked if c.conf * 100 >= min_conf]
    if not strong:
        return PageNumberResult("UNREADABLE", candidates=ranked, warnings=warnings)
    top = strong[0]
    if len(strong) > 1 and top.score < 1.6 * strong[1].score:
        return PageNumberResult("AMBIGUOUS", candidates=ranked, warnings=warnings)
    return PageNumberResult("DETECTED", top.value, round(top.conf, 2), top.region, ranked, warnings)


@dataclass
class Validation:
    decision: str            # MATCH | MISMATCH | UNCERTAIN
    expected: int
    detected: int | None
    can_read: bool


def validate(expected: int, result: PageNumberResult) -> Validation:
    if result.status == "DETECTED" and result.page == expected:
        return Validation("MATCH", expected, result.page, True)
    if result.status == "DETECTED":
        return Validation("MISMATCH", expected, result.page, False)
    return Validation("UNCERTAIN", expected, None, False)
