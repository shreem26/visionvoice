"""OCR/page-number tests on generated pages + an end-to-end run against the simulated boards."""
import socket
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

pytesseract = pytest.importorskip("pytesseract")
try:
    pytesseract.get_tesseract_version()
except Exception:  # pragma: no cover
    pytest.skip("tesseract not installed", allow_module_level=True)

import make_test_pages  # noqa: E402
import sim_devices  # noqa: E402

from visionvoice.config import load_config  # noqa: E402
from visionvoice.vision.ocr import decode_image, run_ocr  # noqa: E402
from visionvoice.vision.page_number import detect_page_number  # noqa: E402


@pytest.fixture(scope="session")
def pages(tmp_path_factory):
    return make_test_pages.generate(tmp_path_factory.mktemp("pages"))


def detect(path):
    return detect_page_number(run_ocr(decode_image(Path(path).read_bytes())))


@pytest.mark.parametrize("name,status,page", [
    ("page_12.jpg", "DETECTED", 12), ("page_13.jpg", "DETECTED", 13), ("page_14.jpg", "DETECTED", 14),
    ("page_15.jpg", "DETECTED", 15), ("page_16.jpg", "DETECTED", 16), ("page_18.jpg", "DETECTED", 18),
    ("page_30_chapter_header.jpg", "DETECTED", 30), ("page_31_running_header.jpg", "DETECTED", 31),
    ("no_number.jpg", "MISSING", None), ("number_in_body_only.jpg", "MISSING", None),
    ("spread_40_41.jpg", "AMBIGUOUS", None),
])
def test_page_number_detection(pages, name, status, page):
    r = detect(pages / name)
    assert r.status == status, r
    assert r.page == page


def test_blurry_page_is_never_detected_as_wrong_number(pages):
    r = detect(pages / "page_12_very_blurry.jpg")
    assert r.status != "DETECTED" or r.page == 12


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeSTT:
    available = True

    def __init__(self):
        self.next = ""

    def load(self):
        pass

    def transcribe(self, pcm):
        from visionvoice.audio.stt import Transcription

        return Transcription("OK", self.next) if self.next else Transcription("NO_SPEECH")


def test_end_to_end_with_simulated_boards(pages, tmp_path, monkeypatch):
    """Voice 'read pages 12 to 13' -> captures 12, wrong 15, 13 -> summary. Uses the real protocol over TCP/HTTP."""
    from visionvoice.app import App

    cam_port, mic_port = free_port(), free_port()
    cam, mic, httpd = sim_devices.start(str(pages), None, cam_port, mic_port, udp_port=0, realtime=False)
    cfg = load_config(tmp_path / "none.toml")
    cfg["devices"]["cam"] = f"http://127.0.0.1:{cam_port}"
    cfg["devices"]["mic"] = f"127.0.0.1:{mic_port}"
    cfg["debug"]["captures_dir"] = str(tmp_path / "caps")
    cfg["debug"]["history_db"] = str(tmp_path / "history.db")
    cfg["camera"]["controls"] = {"vflip": 1}
    app = App(cfg, speak=False)
    spoken = []
    monkeypatch.setattr(app.speaker, "speak", lambda text, block=False: spoken.append(text))
    app.stt = FakeSTT()
    monkeypatch.setattr("builtins.input", lambda *a: time.sleep(3600))
    app._connect_devices()
    assert app.mic.wait_connected(5)
    assert cam.controls == {"vflip": 1}  # laptop pushed its camera settings

    def voice(text, held=1500):
        app.stt.next = text
        mic.press(held)
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                src, ev = app.events.get(timeout=0.2)
            except Exception:
                continue
            if src == "mic" and ev.kind == "utterance":
                app.on_mic(ev)
                return
        raise AssertionError("no utterance arrived")

    voice("Read pages twelve to thirteen.")
    assert "Reading pages 12 to 13" in spoken[-1]

    cam.select("page_12.jpg")
    voice("capture")
    assert spoken[-1].startswith("Page 12.") and "Turn to page 13" in spoken[-1]

    cam.select("page_15.jpg")
    voice("", held=150)  # short tap on the button = capture
    assert "Go back 2 pages to reach page 13" in spoken[-1]
    assert "I did not read it" in spoken[-1]

    cam.select("no_number.jpg")
    voice("next")
    assert "couldn't find a page number" in spoken[-1]

    cam.select("page_13.jpg")
    voice("next")
    assert spoken[-1].startswith("Page 13.") and "Finished pages 12 to 13" in spoken[-1]

    voice("summarize Mira")
    assert spoken[-1].startswith("Based on pages 12 to 13 only")
    voice("who is the dragon")
    assert "couldn't find dragon" in spoken[-1]
    voice("")  # silence
    assert "didn't hear anything" in spoken[-1]
    assert len(list((tmp_path / "caps").glob("page_*.jpg"))) == 4
    app.mic.close()
    httpd.shutdown()
