"""Focused character/object summaries, grounded only in the given page texts."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

try:
    from rapidfuzz.distance import Levenshtein
except ImportError:  # pragma: no cover
    Levenshtein = None

PRONOUNS = ("he ", "she ", "it ", "they ", "his ", "her ", "its ", "their ", "him ", "them ")
ABBREV = re.compile(r"\b(Mr|Mrs|Ms|Dr|St|Prof|Sr|Jr)\.")


@dataclass
class Evidence:
    page: int | None
    sentence: str
    kind: str  # direct | partial | pronoun


@dataclass
class SummaryResult:
    status: str  # ANSWERED | NOT_FOUND | INSUFFICIENT_EVIDENCE
    target: str
    speech: str
    evidence: list[Evidence] = field(default_factory=list)
    provider: str = "extractive"
    warnings: list[str] = field(default_factory=list)


def clean_ocr_text(text: str) -> str:
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # join hyphenated line breaks
    text = re.sub(r"\n{2,}", "   ", text)     # keep paragraph breaks as sentence boundaries
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    t = ABBREV.sub(lambda m: m.group(1) + "<DOT>", clean_ocr_text(text))
    t = re.sub(r"([.!?][\"'\u201d\u2019]?)\s+(?=[\"'\u201c\u2018]?[A-Z0-9])", "\\1\u2029", t)
    parts = re.split(r"\s*\u2029\s*", t)
    return [p.replace("<DOT>", ".").strip() for p in parts if p and p.strip()]


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", s.lower())


def _variants(target: str) -> list[str]:
    t = target.lower().strip()
    v = {t, re.sub(r"'s$", "", t)}
    if t.endswith("s"):
        v.add(t[:-1])
    else:
        v.add(t + "s")
    return [x for x in v if x]


def _mention(sentence: str, target: str) -> tuple[str | None, bool]:
    """Return ('direct'|'partial'|None, fuzzy_used)."""
    low = sentence.lower()
    for v in _variants(target):
        if re.search(r"\b" + re.escape(v) + r"(?:'s)?\b", low):
            return "direct", False
    ttoks = _tokens(target)
    if Levenshtein and len(ttoks) == 1 and len(ttoks[0]) >= 4:
        name = ttoks[0]
        allowed = 1 if len(name) <= 6 else 2
        # only capitalised words: OCR-garbled names, not ordinary words that look similar
        for tok in re.findall(r"\b[A-Z][\w']+", sentence):
            tok = re.sub(r"'s$", "", tok).lower()
            if abs(len(tok) - len(name)) <= allowed and Levenshtein.distance(tok, name) <= allowed:
                return "direct", True
    if len(ttoks) > 1:  # "magic ring" -> head noun "ring"
        head = ttoks[-1]
        if len(head) >= 3 and re.search(r"\b" + re.escape(head) + r"s?\b", low):
            return "partial", False
    return None, False


def find_evidence(target: str, pages: list[tuple[int | None, str]]) -> tuple[list[Evidence], list[str]]:
    ev: list[Evidence] = []
    warnings: list[str] = []
    for page, text in pages:
        sents = split_sentences(text)
        for i, s in enumerate(sents):
            kind, fuzzy = _mention(s, target)
            if kind:
                if fuzzy and "fuzzy name match used (OCR errors)" not in warnings:
                    warnings.append("fuzzy name match used (OCR errors)")
                ev.append(Evidence(page, s, kind))
                if i + 1 < len(sents):
                    nxt = sents[i + 1]
                    if nxt.lower().startswith(PRONOUNS) and not _mention(nxt, target)[0]:
                        ev.append(Evidence(page, nxt, "pronoun"))
    # de-duplicate, keep order
    seen = set()
    uniq = []
    for e in ev:
        if (e.page, e.sentence) not in seen:
            seen.add((e.page, e.sentence))
            uniq.append(e)
    return uniq, warnings


def _pages_phrase(pages: list[int | None]) -> str:
    ps = sorted({p for p in pages if p is not None})
    if not ps:
        return "this capture"
    if len(ps) == 1:
        return f"page {ps[0]}"
    if ps == list(range(ps[0], ps[-1] + 1)):
        return f"pages {ps[0]} to {ps[-1]}"
    return "pages " + ", ".join(map(str, ps[:-1])) + f" and {ps[-1]}"


def _trim_words(text: str, n: int) -> str:
    w = text.split()
    return text if len(w) <= n else " ".join(w[:n]) + " ..."


class Summarizer:
    def __init__(self, provider: str = "extractive", model: str = "", max_words: int = 60) -> None:
        self.provider = provider
        self.model = model
        self.max_words = max_words

    def summarize(self, target: str, pages: list[tuple[int | None, str]]) -> SummaryResult:
        scope = _pages_phrase([p for p, _ in pages])
        if not pages or not any(t.strip() for _, t in pages):
            return SummaryResult("INSUFFICIENT_EVIDENCE", target, "I have no captured text yet. Read a page first.")
        ev, warnings = find_evidence(target, pages)
        direct = [e for e in ev if e.kind == "direct"]
        if not ev:
            return SummaryResult("NOT_FOUND", target, f"I couldn't find {target} on {scope}.", warnings=warnings)
        words = sum(len(e.sentence.split()) for e in ev)
        if not direct and words < 12 or words < 8:
            return SummaryResult(
                "INSUFFICIENT_EVIDENCE", target,
                f"{target} is mentioned on {scope}, but there isn't enough detail to summarize. "
                "Capture more pages and ask again.", ev, warnings=warnings,
            )
        prefix = f"Based on {scope} only: "
        if self.provider == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
            res = self._llm(target, ev, prefix)
            if res:
                res.warnings = warnings + res.warnings
                return res
            warnings.append("AI summary failed or was not grounded; used extractive summary")
        chosen = sorted(ev[:4], key=lambda e: ev.index(e))
        body = _trim_words(" ".join(e.sentence for e in chosen), self.max_words)
        return SummaryResult("ANSWERED", target, prefix + body, chosen, "extractive", warnings)

    # ---- optional AI provider with a grounding guard ----
    def _llm(self, target: str, ev: list[Evidence], prefix: str) -> SummaryResult | None:
        try:
            import anthropic
        except ImportError:
            return None
        numbered = "\n".join(f"[{i}] (page {e.page}) {e.sentence}" for i, e in enumerate(ev))
        prompt = (
            f"Summarize what these sentences say about '{target}'. Use ONLY these sentences, add no outside "
            f"knowledge, at most {self.max_words} words, plain spoken English.\n\n{numbered}\n\n"
            'Reply with JSON only: {"answer": "...", "evidence_ids": [numbers you used]}'
        )
        try:
            client = anthropic.Anthropic()
            msg = client.messages.create(model=self.model, max_tokens=400, messages=[{"role": "user", "content": prompt}])
            raw = "".join(getattr(b, "text", "") for b in msg.content)
            data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
            answer = str(data["answer"]).strip()
            ids = [int(i) for i in data.get("evidence_ids", [])]
        except Exception:
            return None
        if not answer or not ids or any(i < 0 or i >= len(ev) for i in ids):
            return None
        if len(answer.split()) > self.max_words * 1.3:
            return None
        source = " ".join(e.sentence for e in ev).lower()
        for name in re.findall(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}\b", answer):
            if name.lower() not in source and name not in ("I",):
                return None  # names something not in the evidence -> reject
        used = [ev[i] for i in sorted(set(ids))]
        return SummaryResult("ANSWERED", target, prefix + answer, used, "anthropic")
