"""Dashboard API tests: same backend rules apply to everything sent from the browser."""
import io
import json
import sys
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

from visionvoice.app import App  # noqa: E402
from visionvoice.config import load_config  # noqa: E402
from visionvoice.store import History  # noqa: E402
from visionvoice.web import server as web_server  # noqa: E402


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    return make_test_pages.generate(tmp_path_factory.mktemp("pages"))


@pytest.fixture
def env(tmp_path, pages, monkeypatch):
    cfg = load_config(tmp_path / "none.toml")
    cfg["debug"].update(captures_dir=str(tmp_path / "caps"), history_db=str(tmp_path / "h.db"))
    app = App(cfg, use_devices=False, speak=False)
    spoken = []
    monkeypatch.setattr(app.speaker, "speak", lambda text, block=False: spoken.append(text))
    monkeypatch.setattr(web_server, "SAMPLES_DIR", pages)
    client = web_server.create_server(app).test_client()

    def drain():
        while app.step(0.05):
            pass

    return app, client, spoken, drain, pages


def test_index_and_state(env):
    app, client, *_ = env
    assert b"VisionVoice" in client.get("/").data
    s = client.get("/api/state").get_json()
    assert set(s) >= {"devices", "session", "last_capture", "last_summary", "stats", "feed", "captures"}
    assert s["session"] is None and s["stats"]["captures"] == 0


def test_full_flow_from_dashboard(env):
    app, client, spoken, drain, pages = env
    assert client.post("/api/command", json={"text": "read pages 12 to 13"}).status_code == 202
    drain()
    assert client.get("/api/state").get_json()["session"]["expected"] == 12

    client.post("/api/capture", json={"sample": "page_12.jpg"})
    drain()
    s = client.get("/api/state").get_json()
    lc = s["last_capture"]
    assert lc["decision"] == "MATCH" and lc["detected"] == 12 and "Mira" in lc["text"]
    assert s["session"]["verified"] == [12]
    assert client.get(f"/captures/{lc['id']}/annotated.jpg").data[:2] == b"\xff\xd8"
    assert client.get(f"/captures/{lc['id']}/photo.jpg").status_code == 200

    # wrong page uploaded as a file: refused, no page text leaves the backend
    data = (pages / "page_15.jpg").read_bytes()
    r = client.post("/api/capture", data={"photo": (io.BytesIO(data), "p15.jpg", "image/jpeg")},
                    content_type="multipart/form-data")
    assert r.status_code == 202
    drain()
    lc = client.get("/api/state").get_json()["last_capture"]
    assert lc["decision"] == "MISMATCH" and lc["text"] == "" and "Go back 2 pages" in lc["message"]
    assert lc["source"] == "upload"

    client.post("/api/capture", json={"sample": "page_13.jpg"})
    client.post("/api/command", json={"text": "summarize the silver key"})
    drain()
    s = client.get("/api/state").get_json()
    assert s["session"]["status"] == "COMPLETED"
    assert s["last_summary"]["status"] == "ANSWERED" and s["last_summary"]["pages"] == [12, 13]
    assert s["stats"] == {**s["stats"], "match": 2, "mismatch": 1, "captures": 3, "summaries": 1}
    kinds = [h["kind"] for h in client.get("/api/history").get_json()]
    assert kinds.count("capture") == 3 and "summary" in kinds
    assert any(e["kind"] == "speech" for e in s["feed"])


def test_capture_without_session_is_refused(env):
    app, client, spoken, drain, _ = env
    client.post("/api/capture", json={"sample": "page_12.jpg"})
    drain()
    assert "Which page should I read" in spoken[-1]
    assert client.get("/api/state").get_json()["last_capture"] is None


def test_bad_requests(env):
    app, client, *_ = env
    assert client.post("/api/command", json={"text": ""}).status_code == 400
    assert client.post("/api/command", data="not json").status_code == 400
    assert client.post("/api/command", json={"text": "x" * 301}).status_code == 400
    r = client.post("/api/capture", data={"photo": (io.BytesIO(b"hello"), "a.txt", "text/plain")},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "UNSUPPORTED_TYPE"
    assert client.post("/api/capture", json={"sample": "../../etc/passwd"}).status_code == 404
    assert client.get("/captures/nope/annotated.jpg").status_code == 404
    assert client.get("/captures/x/../../secret.jpg").status_code == 404


def test_samples_and_events_stream(env):
    app, client, *_ = env
    assert "page_12.jpg" in client.get("/api/samples").get_json()
    resp = client.get("/api/events", buffered=False)
    app.bus.publish("info", text="hello", level="info")
    got = ""
    for chunk in resp.response:
        got += chunk.decode() if isinstance(chunk, bytes) else chunk
        if "hello" in got:
            break
    assert "hello" in got
    resp.close()


def test_history_store(tmp_path):
    h = History(tmp_path / "x.db")
    h.add("capture", source="voice", expected_page=3, detected_page=3, decision="MATCH", ocr_ms=900)
    h.add("capture", source="tap", expected_page=4, detected_page=6, decision="MISMATCH", ocr_ms=1100)
    h.add("summary", detail="mira", decision="ANSWERED")
    st = h.stats()
    assert st["match"] == 1 and st["mismatch"] == 1 and st["avg_ocr_ms"] == 1000 and st["summaries"] == 1
    assert [r["kind"] for r in h.recent()] == ["summary", "capture", "capture"]
    # survives reopening
    assert len(History(tmp_path / "x.db").recent()) == 3
