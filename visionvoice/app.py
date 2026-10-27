"""VisionVoice v1 — main program.

Voice (mic board) or typed commands -> camera capture -> OCR -> page check -> speech.
Run:  python -m visionvoice            (see README.md for options)
"""
from __future__ import annotations

import argparse
import logging
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .audio.stt import make_stt
from .audio.tts import Speaker
from .audio.wav import save_wav, stats
from .config import load_config
from .hw.camera import CameraClient, CameraError, FileCamera
from .hw.discovery import discover, resolve_mdns
from .hw.mic import MicClient, MicEvent
from .logic.commands import HELP_TEXT, Command, parse_command
from .logic.session import RangeError, ReadingSession
from .logic.summary import Summarizer
from .vision.ocr import OcrError, decode_image, readable_text, run_ocr, setup_tesseract
from .vision.page_number import detect_page_number

log = logging.getLogger("visionvoice")

for _s in (sys.stdout, sys.stderr):  # Windows consoles: never crash on emoji
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass


class App:
    def __init__(self, cfg: dict, use_devices: bool = True, image: str | None = None, speak: bool = True) -> None:
        self.cfg = cfg
        self.events: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.speaker = Speaker(cfg["tts"]["engine"] if speak else "print", cfg["tts"]["rate"])
        self.summarizer = Summarizer(cfg["summary"]["provider"], cfg["summary"]["model"], cfg["summary"]["max_words"])
        self.stt = make_stt(cfg["stt"])
        self.session: ReadingSession | None = None
        self.last_capture: tuple[int | None, str] | None = None  # (detected page, text) when no session
        self.camera = FileCamera(image) if image else None
        self.mic: MicClient | None = None
        self.use_devices = use_devices
        self.running = True
        self.capture_dir = Path(cfg["debug"]["captures_dir"])

    # ---------- startup ----------
    def start(self) -> None:
        try:
            v = setup_tesseract(self.cfg["ocr"]["tesseract_cmd"])
            print(f"[ok] Tesseract {v}")
        except OcrError as e:
            print(f"[!!] {e}")
        if getattr(self.stt, "available", False):
            print("[..] loading speech recognition model in the background (first time downloads it)")
            threading.Thread(target=self._preload_stt, daemon=True).start()
        else:
            print("[!!] speech recognition not installed: typed commands only (pip install faster-whisper)")
        if self.use_devices:
            self._connect_devices()
        threading.Thread(target=self._keyboard, daemon=True).start()
        self.say("VisionVoice ready. Say, read page, followed by a number. Or say help.")

    def _preload_stt(self) -> None:
        try:
            self.stt.load()
            print("[ok] speech recognition ready")
        except Exception as e:  # pragma: no cover
            print(f"[!!] speech recognition failed to load: {e}")

    def _connect_devices(self) -> None:
        dcfg = self.cfg["devices"]
        cam_addr, mic_addr = dcfg["cam"], dcfg["mic"]
        if not cam_addr or not mic_addr:
            hosts = [a.split("//")[-1].split(":")[0] for a in (cam_addr, mic_addr) if a]
            print("[..] looking for the camera and mic boards on the network...")
            found = discover(dcfg["discovery_timeout"], hosts)
            if not cam_addr:
                if "cam" in found:
                    cam_addr = f"http://{found['cam']['ip']}:{found['cam'].get('port', 80)}"
                elif ip := resolve_mdns("vv-cam.local"):
                    cam_addr = f"http://{ip}"
            if not mic_addr:
                if "mic" in found:
                    mic_addr = f"{found['mic']['ip']}:{found['mic'].get('port', 5001)}"
                elif ip := resolve_mdns("vv-mic.local"):
                    mic_addr = f"{ip}:5001"
        if not self.camera:
            if cam_addr:
                cam = CameraClient(cam_addr, self.cfg["camera"]["timeout"])
                try:
                    st = cam.status()
                    print(f"[ok] camera {st.get('id')} at {cam.base_url} (fw {st.get('fw')}, camera_ok={st.get('camera_ok')})")
                    res = cam.apply_controls(self.cfg["camera"]["controls"])
                    if res and not all(res.values()):
                        print(f"[!!] some camera settings were rejected: {res}")
                    self.camera = cam
                except CameraError as e:
                    print(f"[!!] {e}")
            else:
                print("[!!] camera board not found. Type 'capture <file.jpg>' to test with a photo.")
        if mic_addr:
            host, _, port = mic_addr.partition(":")
            self.mic = MicClient(host, int(port or 5001), self.cfg["mic"], on_button_down=self.speaker.stop).start()
            if self.mic.wait_connected(5):
                print(f"[ok] mic board {self.mic.hello.get('id')} at {mic_addr} (fw {self.mic.hello.get('fw')})")
            else:
                print(f"[..] mic board at {mic_addr} not answering yet; will keep trying in the background")
            threading.Thread(target=self._pump_mic, daemon=True).start()
        else:
            print("[!!] mic board not found. Typed commands still work.")

    # ---------- input threads ----------
    def _pump_mic(self) -> None:
        while self.running and self.mic:
            try:
                ev = self.mic.events.get(timeout=0.5)
            except queue.Empty:
                continue
            if ev.kind in ("utterance", "connected", "disconnected"):
                self.events.put(("mic", ev))

    def _keyboard(self) -> None:
        print("Type a command (or 'help') and press Enter.")
        while self.running:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                self.events.put(("key", "quit"))
                return
            if line.strip():
                self.events.put(("key", line.strip()))

    # ---------- main loop ----------
    def run(self) -> None:
        self.start()
        while self.running:
            try:
                src, item = self.events.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if src == "mic":
                    self.on_mic(item)  # type: ignore[arg-type]
                else:
                    self.on_text(str(item))
            except Exception as e:  # keep running whatever happens
                log.exception("error")
                self.say(f"Sorry, something went wrong: {e}")
        self.speaker.stop()
        if self.mic:
            self.mic.close()

    def on_mic(self, ev: MicEvent) -> None:
        if ev.kind == "connected":
            print(f"[ok] mic board connected ({ev.data.get('id')})")
            return
        if ev.kind == "disconnected":
            print(f"[!!] mic board disconnected: {ev.data.get('reason')} (reconnecting...)")
            return
        st = stats(ev.pcm)
        held = int(ev.data.get("held_ms", 0))
        if self.cfg["debug"]["save_captures"]:
            save_wav(self.capture_dir / f"voice_{_stamp()}.wav", ev.pcm)
        if held and held < self.cfg["mic"]["tap_ms"]:
            print("[mic] short tap -> capture")
            self.execute(Command("CAPTURE", raw="(button tap)"))
            return
        if not getattr(self.stt, "available", False):
            self.say("Speech recognition is not installed. Please type the command.")
            return
        if self.mic:
            self.mic.set_led(MicClient.LED_FAST)
        t0 = time.time()
        tr = self.stt.transcribe(ev.pcm)
        if self.mic:
            self.mic.set_led(MicClient.LED_PULSE)
        log.info("stt %s %.1fs audio=%.1fs rms=%.0f: %s", tr.status, time.time() - t0, st["seconds"], st["rms"], tr.text)
        if tr.status == "NO_SPEECH":
            self.say("I didn't hear anything. Hold the button while you speak.")
            return
        if tr.status == "ERROR":
            self.say("Speech recognition failed. Please try again.")
            print(f"[!!] {tr.error}")
            return
        print(f'[heard] "{tr.text}"')
        self.on_text(tr.text, spoken=True)

    def on_text(self, text: str, spoken: bool = False) -> None:
        low = text.lower()
        if not spoken and low.startswith("capture ") and len(text) > 8:  # typed: capture <file>
            path = text[8:].strip().strip('"')
            if Path(path).exists():
                self.execute(Command("CAPTURE", raw=text), image_file=path)
            else:
                self.say(f"File not found: {path}")
            return
        if not spoken and low == "devices":
            self._connect_devices()
            return
        cmd = parse_command(text)
        self.execute(cmd)

    # ---------- actions ----------
    def execute(self, cmd: Command, image_file: str | None = None) -> None:
        log.info("command %s pages=%s target=%s raw=%r", cmd.intent, cmd.pages, cmd.target, cmd.raw)
        if cmd.intent == "READ_PAGES":
            try:
                self.session = ReadingSession.create(*cmd.pages, max_pages=self.cfg["session"]["max_range_pages"])
            except RangeError as e:
                self.say(str(e))
                return
            self.say(f"Reading {self.session.describe()}. Place page {self.session.start} under the camera and say capture.")
        elif cmd.intent == "CAPTURE":
            self.capture(image_file)
        elif cmd.intent == "SUMMARIZE":
            self.summarize(cmd.target)
        elif cmd.intent == "REPEAT":
            self.say(self.speaker.last or "There is nothing to repeat.")
        elif cmd.intent == "STOP":
            self.speaker.stop()
        elif cmd.intent == "CANCEL":
            self.session = None
            self.say("Cancelled.")
        elif cmd.intent == "STATUS":
            s = self.session
            if s and s.status == "ACTIVE":
                self.say(f"Reading {s.describe()}. Waiting for page {s.expected}.")
            elif s:
                self.say(f"Finished {s.describe()}.")
            else:
                self.say("No pages requested. Say read page, followed by a number.")
        elif cmd.intent == "HELP":
            self.say(HELP_TEXT)
        elif cmd.intent == "QUIT":
            self.say("Goodbye.", block=True)
            self.running = False
        else:
            hint = {"no page number": "Which page? Say, read page, and a number.", "no target": "Summarize who or what?"}
            self.say(hint.get(cmd.note, "Sorry, I didn't understand. Say help to hear the commands."))

    def capture(self, image_file: str | None = None) -> None:
        cam = FileCamera(image_file) if image_file else self.camera
        if cam is None:
            self.say("The camera is not connected.")
            return
        if self.session is None or self.session.status != "ACTIVE":
            self.say("Which page should I read? Say, read page, and a number, then capture.")
            return
        c = self.cfg["camera"]
        try:
            photo = cam.capture(size=c["size"], quality=c["quality"], flash=c["flash"], skip=c["skip"])
        except CameraError as e:
            self.say("I couldn't take a photo. Check the camera is switched on.")
            print(f"[!!] {e}")
            return
        if self.cfg["debug"]["save_captures"]:
            self.capture_dir.mkdir(parents=True, exist_ok=True)
            (self.capture_dir / f"page_{_stamp()}.jpg").write_bytes(photo.jpeg)
        t0 = time.time()
        try:
            img = decode_image(photo.jpeg, c["rotate"])
            ocr = run_ocr(img, self.cfg["ocr"]["lang"])
        except OcrError as e:
            self.say("I couldn't read that photo. Please capture again.")
            print(f"[!!] {e}")
            return
        det = detect_page_number(ocr, self.cfg["ocr"]["band"], self.cfg["ocr"]["min_conf"], self.cfg["ocr"]["lang"])
        print(f"[page] {det.status} page={det.page} conf={det.confidence} region={det.region} "
              f"candidates={[(x.value, x.region, round(x.conf, 2)) for x in det.candidates[:4]]} "
              f"(photo {photo.resolution} {photo.seconds:.1f}s, ocr {time.time() - t0:.1f}s)")
        out = self.session.submit(det, readable_text(ocr.text))
        log.info("capture expected=%s decision=%s detected=%s", out.validation.expected, out.validation.decision, out.validation.detected)
        self.say(out.speech)

    def summarize(self, target: str) -> None:
        if self.session and self.session.verified:
            pages = sorted(self.session.verified.items())
        else:
            self.say("Read at least one page first, then ask me to summarize.")
            return
        res = self.summarizer.summarize(target, pages)
        log.info("summary %s target=%s provider=%s warnings=%s", res.status, target, res.provider, res.warnings)
        for e in res.evidence:
            print(f"   evidence (p{e.page}, {e.kind}): {e.sentence}")
        self.say(res.speech)

    def say(self, text: str, block: bool = False) -> None:
        self.speaker.speak(text, block=block)


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="visionvoice", description="VisionVoice reading assistant v1")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--no-devices", action="store_true", help="don't look for the ESP32 boards (typed commands + files)")
    ap.add_argument("--image", help="use this image file as the camera")
    ap.add_argument("--quiet", action="store_true", help="print instead of speaking")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    logging.basicConfig(
        filename=cfg["debug"]["log_file"], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    app = App(cfg, use_devices=not args.no_devices, image=args.image, speak=not args.quiet)
    try:
        app.run()
    except KeyboardInterrupt:
        app.speaker.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
