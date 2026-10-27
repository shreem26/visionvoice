"""Parse typed or spoken commands into intents."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .numbers import words_to_digits

SUMMARY_PREFIXES = (
    "summarize", "summarise", "summary of", "explain", "who is", "who's", "what is", "what's",
    "tell me about", "describe", "talk about",
)
ARTICLES = ("the ", "a ", "an ")


@dataclass
class Command:
    intent: str  # READ_PAGES SUMMARIZE CAPTURE REPEAT STOP CANCEL STATUS HELP QUIT UNKNOWN
    pages: tuple[int, int] | None = None
    target: str = ""
    raw: str = ""
    note: str = ""


def _clean(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^\w\s'-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_command(text: str) -> Command:
    raw = text
    t = _clean(text)
    if not t:
        return Command("UNKNOWN", raw=raw, note="empty")

    for p in SUMMARY_PREFIXES:
        if t.startswith(p + " ") or t == p:
            target = t[len(p):].strip(" '")
            target = re.sub(r"^(about|on|the role of|role of)\s+", "", target)
            for a in ARTICLES:
                if target.startswith(a):
                    target = target[len(a):]
            target = re.sub(r"'s$", "", target).strip()
            if not target:
                return Command("UNKNOWN", raw=raw, note="no target")
            return Command("SUMMARIZE", target=target, raw=raw)

    n = re.sub(r"(\d)\s*-\s*(\d)", r"\1 to \2", t)
    n = words_to_digits(n)

    m = re.search(r"\b(?:from\s+)?pages?\s+(\d{1,4})\s+(?:to|through|thru|till|until|and)\s+(?:page\s+)?(\d{1,4})\b", n) or \
        re.search(r"^(?:read|go to)\s+(\d{1,4})\s+(?:to|through|thru|till|until)\s+(\d{1,4})$", n)
    if m:
        return Command("READ_PAGES", pages=(int(m.group(1)), int(m.group(2))), raw=raw)
    m = re.search(r"\bpages?\s+(?:number\s+)?(\d{1,4})\b", n)
    if m:
        return Command("READ_PAGES", pages=(int(m.group(1)), int(m.group(1))), raw=raw)
    if re.search(r"\bpages?\b", n) and re.match(r"^(read|go to|open)\b", n):
        return Command("UNKNOWN", raw=raw, note="no page number")

    simple = [
        ("CAPTURE", r"^(capture|scan|next|next page|take (a )?(photo|picture)|snap|shoot|done|turned|ok next)$"),
        ("REPEAT", r"^(repeat|again|say (that|it) again|read (it )?again|what)$"),
        ("STOP", r"^(stop|quiet|pause|shut up|be quiet|silence)$"),
        ("CANCEL", r"^(cancel|reset|start over)$"),
        ("STATUS", r"^(status|where am i|which page|what page)$"),
        ("HELP", r"^(help|what can i say|commands)$"),
        ("QUIT", r"^(quit|exit|goodbye|bye)$"),
    ]
    for intent, pat in simple:
        if re.match(pat, n):
            return Command(intent, raw=raw)
    return Command("UNKNOWN", raw=raw)


HELP_TEXT = (
    "You can say: read page 12. Read pages 12 to 15. Capture, or next. "
    "Summarize, followed by a name, for example summarize Cinderella. Repeat. Stop. Cancel. Status."
)
