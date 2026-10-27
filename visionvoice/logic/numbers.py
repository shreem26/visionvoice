"""Turn spoken number words into digits: 'one hundred and five' -> '105'."""
from __future__ import annotations

UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
NUMBER_WORDS = set(UNITS) | set(TENS) | {"hundred", "thousand"}


def _value(tokens: list[str]) -> int:
    total, current = 0, 0
    for t in tokens:
        if t in UNITS:
            current += UNITS[t]
        elif t in TENS:
            current += TENS[t]
        elif t == "hundred":
            current = (current or 1) * 100
        elif t == "thousand":
            total += (current or 1) * 1000
            current = 0
    return total + current


def _starts_new_number(run: list[str], tok: str) -> bool:
    """'twelve fifteen' is two numbers; 'twenty one' is one."""
    if not run:
        return False
    last = run[-1]
    if tok in UNITS and last in TENS and UNITS[tok] < 10:
        return False
    if tok in ("hundred", "thousand"):
        return False
    if last in ("hundred", "thousand"):
        return False
    return True


def words_to_digits(text: str) -> str:
    """Replace each run of number words with its digits. Expects lower-case, single-spaced text."""
    toks = text.replace("-", " ").split()
    out: list[str] = []
    run: list[str] = []
    for i, tok in enumerate(toks):
        if tok in NUMBER_WORDS:
            if _starts_new_number(run, tok):
                out.append(str(_value(run)))
                run = []
            run.append(tok)
        elif tok == "and" and run and run[-1] in ("hundred", "thousand") and i + 1 < len(toks) and toks[i + 1] in NUMBER_WORDS:
            continue
        else:
            if run:
                out.append(str(_value(run)))
                run = []
            out.append(tok)
    if run:
        out.append(str(_value(run)))
    return " ".join(out)
