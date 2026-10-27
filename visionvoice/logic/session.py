"""Reading session for a single page or a range, walked strictly in order."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..vision.page_number import PageNumberResult, Validation, validate


class RangeError(ValueError):
    pass


@dataclass
class CaptureOutcome:
    validation: Validation
    speech: str              # the ONLY text to speak; page text only on MATCH
    page_text: str = ""      # set only on MATCH
    done: bool = False


@dataclass
class ReadingSession:
    start: int
    end: int
    index: int = 0
    verified: dict[int, str] = field(default_factory=dict)  # page -> OCR text (MATCH only)
    uncertain_streak: int = 0
    status: str = "ACTIVE"  # ACTIVE | COMPLETED | CANCELLED

    @classmethod
    def create(cls, start: int, end: int, max_pages: int = 30) -> "ReadingSession":
        if start < 1 or end < 1:
            raise RangeError("Page numbers must be 1 or more.")
        if end < start:
            raise RangeError(f"The range must go forward. You said {start} to {end}.")
        if end - start + 1 > max_pages:
            raise RangeError(f"That is {end - start + 1} pages. The limit is {max_pages} pages at a time.")
        return cls(start, end)

    @property
    def pages(self) -> list[int]:
        return list(range(self.start, self.end + 1))

    @property
    def expected(self) -> int | None:
        return self.start + self.index if self.status == "ACTIVE" else None

    def describe(self) -> str:
        return f"page {self.start}" if self.start == self.end else f"pages {self.start} to {self.end}"

    def submit(self, detection: PageNumberResult, page_text: str) -> CaptureOutcome:
        if self.status != "ACTIVE":
            raise RuntimeError("session is not active")
        exp = self.expected
        v = validate(exp, detection)
        if v.decision == "MATCH":
            self.uncertain_streak = 0
            self.verified[exp] = page_text
            self.index += 1
            body = page_text.strip() or "This page has no readable text."
            if self.start + self.index > self.end:
                self.status = "COMPLETED"
                tail = f"End of page {exp}. Finished {self.describe()}."
                return CaptureOutcome(v, f"Page {exp}. {body} ... {tail}", page_text, done=True)
            tail = f"End of page {exp}. Turn to page {exp + 1} and say capture."
            return CaptureOutcome(v, f"Page {exp}. {body} ... {tail}", page_text)

        if v.decision == "MISMATCH":
            self.uncertain_streak = 0
            d = v.detected
            if d in self.verified and d == exp - 1:
                msg = f"This is still page {d}. Please turn to page {exp}."
            elif abs(d - exp) > 50:
                msg = f"I read page {d}, but I expected page {exp}. If that is wrong, please capture again."
            elif d > exp:
                k = d - exp
                msg = f"Wrong page. You are on page {d}. Go back {k} page{'s' if k > 1 else ''} to reach page {exp}."
            else:
                k = exp - d
                msg = f"Wrong page. You are on page {d}. Go forward {k} page{'s' if k > 1 else ''} to reach page {exp}."
            return CaptureOutcome(v, msg + " I did not read it.")

        self.uncertain_streak += 1
        reason = {
            "MISSING": "I couldn't find a page number. Make sure the top and bottom of the page are in view",
            "AMBIGUOUS": "I can see more than one page number. Show only one page",
            "UNREADABLE": "The page number is unclear. Hold the book still and add more light",
        }.get(detection.status, "I couldn't verify the page number")
        msg = f"{reason}, then capture again. I did not read this page."
        if self.uncertain_streak >= 3:
            msg += " You can say cancel to stop."
        return CaptureOutcome(v, msg)
