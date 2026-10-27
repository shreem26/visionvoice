import pytest

from visionvoice.hw.protocol import FrameReader, encode_frame
from visionvoice.logic.commands import parse_command
from visionvoice.logic.numbers import words_to_digits
from visionvoice.logic.session import RangeError, ReadingSession
from visionvoice.logic.summary import Summarizer, split_sentences
from visionvoice.vision.page_number import PageNumberResult


# ---------- protocol ----------
def test_frames_roundtrip_and_split_delivery():
    data = encode_frame("H", '{"a":1}') + encode_frame("A", b"\x01\x02" * 300) + encode_frame("P")
    r = FrameReader()
    out = []
    for i in range(0, len(data), 7):  # arrive in awkward pieces
        out += r.feed(data[i:i + 7])
    assert [t for t, _ in out] == ["H", "A", "P"]
    assert out[1][1] == b"\x01\x02" * 300


def test_frame_reader_resyncs_after_garbage():
    r = FrameReader()
    assert r.feed(b"\x00\x13junk" + encode_frame("S", "{}")) == [("S", b"{}")]


# ---------- numbers / commands ----------
@pytest.mark.parametrize("text,expected", [
    ("fifteen", "15"), ("twenty one", "21"), ("one hundred and five", "105"),
    ("two thousand and twelve", "2012"), ("twelve fifteen", "12 15"), ("page ninety nine", "page 99"),
])
def test_words_to_digits(text, expected):
    assert words_to_digits(text) == expected


@pytest.mark.parametrize("text,intent,pages,target", [
    ("Read page 15.", "READ_PAGES", (15, 15), ""),
    ("read page fifteen", "READ_PAGES", (15, 15), ""),
    ("go to page 7", "READ_PAGES", (7, 7), ""),
    ("Read pages 12 to 15", "READ_PAGES", (12, 15), ""),
    ("pages 12-15", "READ_PAGES", (12, 15), ""),
    ("read pages twelve through fifteen", "READ_PAGES", (12, 15), ""),
    ("from page 12 till 15", "READ_PAGES", (12, 15), ""),
    ("read 12 to 15", "READ_PAGES", (12, 15), ""),
    ("Summarize Cinderella.", "SUMMARIZE", None, "cinderella"),
    ("explain the magic ring", "SUMMARIZE", None, "magic ring"),
    ("who is Oren?", "SUMMARIZE", None, "oren"),
    ("tell me about the three bears", "SUMMARIZE", None, "three bears"),
    ("capture", "CAPTURE", None, ""), ("Next.", "CAPTURE", None, ""), ("take a picture", "CAPTURE", None, ""),
    ("repeat", "REPEAT", None, ""), ("stop", "STOP", None, ""), ("cancel", "CANCEL", None, ""),
    ("where am I", "STATUS", None, ""), ("help", "HELP", None, ""), ("quit", "QUIT", None, ""),
    ("read page", "UNKNOWN", None, ""), ("summarize", "UNKNOWN", None, ""), ("banana", "UNKNOWN", None, ""),
    ("", "UNKNOWN", None, ""),
])
def test_parse_command(text, intent, pages, target):
    c = parse_command(text)
    assert c.intent == intent
    assert c.pages == pages
    assert c.target == target


# ---------- session / guidance ----------
def det(page=None, status="DETECTED"):
    return PageNumberResult(status, page, 0.9 if page else 0.0)


def test_range_in_order_completes():
    s = ReadingSession.create(12, 14)
    for p in (12, 13, 14):
        out = s.submit(det(p), f"text of {p}")
        assert out.validation.can_read and f"text of {p}" in out.speech
    assert s.status == "COMPLETED" and out.done
    assert sorted(s.verified) == [12, 13, 14]


def test_wrong_page_gives_direction_and_reads_nothing():
    s = ReadingSession.create(12, 15)
    s.submit(det(12), "twelve")
    out = s.submit(det(15), "SECRET FIFTEEN TEXT")
    assert out.validation.decision == "MISMATCH" and not out.validation.can_read
    assert "Go back 2 pages to reach page 13" in out.speech
    assert "SECRET" not in out.speech and out.page_text == ""
    assert s.expected == 13
    out = s.submit(det(11), "x")
    assert "Go forward 2 pages" in out.speech


def test_same_page_again():
    s = ReadingSession.create(12, 15)
    s.submit(det(12), "twelve")
    assert "still page 12" in s.submit(det(12), "twelve").speech


@pytest.mark.parametrize("status", ["MISSING", "AMBIGUOUS", "UNREADABLE"])
def test_uncertain_never_reads_or_advances(status):
    s = ReadingSession.create(5, 6)
    out = s.submit(det(None, status), "HIDDEN")
    assert out.validation.decision == "UNCERTAIN" and "HIDDEN" not in out.speech and s.expected == 5
    s.submit(det(None, status), "HIDDEN")
    assert "cancel" in s.submit(det(None, status), "HIDDEN").speech


@pytest.mark.parametrize("a,b", [(15, 12), (0, 3), (-1, 2), (1, 100)])
def test_invalid_ranges(a, b):
    with pytest.raises(RangeError):
        ReadingSession.create(a, b, max_pages=30)


# ---------- summary ----------
PAGES = [
    (12, "Mira lived at the edge of the salt marsh with her grandfather. She carried his nets to the jetty every "
         "morning.\n\nThe boats slipped out into the fog."),
    (13, "One morning Mira found a silver key in the oldest net. It had a tiny crescent moon carved into its handle."),
]


def test_summary_found_and_grounded():
    r = Summarizer().summarize("mira", PAGES)
    assert r.status == "ANSWERED"
    assert r.speech.startswith("Based on pages 12 to 13 only")
    source = " ".join(t for _, t in PAGES).replace("\n", " ")
    for e in r.evidence:
        assert e.sentence in " ".join(split_sentences(source))


def test_summary_object_and_pronoun_follow_up():
    r = Summarizer().summarize("silver key", PAGES)
    assert r.status == "ANSWERED"
    assert any("crescent moon" in e.sentence and e.kind == "pronoun" for e in r.evidence)


def test_summary_not_found_and_ocr_typo():
    assert Summarizer().summarize("dragon", PAGES).status == "NOT_FOUND"
    noisy = [(12, "Mlra carried the nets to the jetty and waited for the boats to return home safely.")]
    r = Summarizer().summarize("mira", noisy)
    assert r.status == "ANSWERED" and r.warnings


def test_summary_insufficient():
    r = Summarizer().summarize("oren", [(3, "Oren waved.")])
    assert r.status == "INSUFFICIENT_EVIDENCE"


def test_summary_needs_text():
    assert Summarizer().summarize("mira", []).status == "INSUFFICIENT_EVIDENCE"
