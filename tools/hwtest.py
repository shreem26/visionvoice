"""VisionVoice hardware acceptance test.

Walks through every hardware function, tells you what to do, and prints a
PASS / FAIL / WARN report (also saved to hwtest_output/report.txt).

  python tools/hwtest.py                 # full interactive test
  python tools/hwtest.py --only cam      # camera only (or: mic)
  python tools/hwtest.py --soak 10       # + 10-minute stability run
  python tools/hwtest.py --reconnect     # + unplug/replug recovery test
  python tools/hwtest.py --auto          # no questions (used with the simulator)
"""
from __future__ import annotations

import argparse
import platform
import queue
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from visionvoice.config import load_config  # noqa: E402
from visionvoice.hw.camera import CameraClient, CameraError  # noqa: E402
from visionvoice.hw.discovery import discover, local_ip  # noqa: E402
from visionvoice.hw.mic import MicClient  # noqa: E402

OUT = Path("hwtest_output")

for _s in (sys.stdout, sys.stderr):  # Windows consoles: never crash on emoji
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, name: str, status: str, detail: str = "", hint: str = "") -> None:
        self.rows.append((name, status, detail, hint))
        icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️ ", "SKIP": "⏭️ "}.get(status, "  ")
        print(f"  {icon} {status:4}  {name}: {detail}")
        if hint and status != "PASS":
            print(f"          → {hint}")

    def text(self) -> str:
        lines = [f"VisionVoice hardware test  {time.strftime('%Y-%m-%d %H:%M:%S')}  ({platform.platform()})", ""]
        for n, s, d, h in self.rows:
            lines.append(f"[{s}] {n}: {d}" + (f"\n        hint: {h}" if h and s != "PASS" else ""))
        counts = {k: sum(1 for r in self.rows if r[1] == k) for k in ("PASS", "WARN", "FAIL", "SKIP")}
        lines += ["", "TOTAL  " + "  ".join(f"{k}={v}" for k, v in counts.items())]
        return "\n".join(lines)

    @property
    def failed(self) -> bool:
        return any(r[1] == "FAIL" for r in self.rows)


class Tester:
    def __init__(self, args, cfg) -> None:
        self.a, self.cfg, self.r = args, cfg, Report()
        self.cam: CameraClient | None = None
        self.mic: MicClient | None = None
        OUT.mkdir(exist_ok=True)

    # ---------- interaction ----------
    def step(self, title: str) -> None:
        print(f"\n=== {title} ===")

    def ask(self, prompt: str) -> str:
        if self.a.auto:
            return ""
        return input(f"  ▶ {prompt} ").strip()

    def yes(self, prompt: str) -> bool | None:
        if self.a.auto:
            return None
        return self.ask(prompt + " [y/n]").lower().startswith("y")

    # ---------- environment ----------
    def env(self) -> None:
        self.step("1. Laptop software")
        self.r.add("Python", "PASS" if sys.version_info >= (3, 10) else "FAIL", sys.version.split()[0], "Install Python 3.10 or newer")
        for mod, need in [("cv2", True), ("numpy", True), ("requests", True), ("pytesseract", True), ("rapidfuzz", False),
                          ("faster_whisper", False)]:
            try:
                __import__(mod)
                self.r.add(f"package {mod}", "PASS", "installed")
            except ImportError:
                self.r.add(f"package {mod}", "FAIL" if need else "WARN", "missing", "pip install -r requirements.txt")
        try:
            from visionvoice.vision.ocr import setup_tesseract

            self.r.add("Tesseract OCR", "PASS", setup_tesseract(self.cfg["ocr"]["tesseract_cmd"]))
        except Exception as e:
            self.r.add("Tesseract OCR", "FAIL", str(e), "Install Tesseract; on Windows set ocr.tesseract_cmd in config.toml")
        self.r.add("Laptop IP", "PASS", local_ip(), "")

    # ---------- discovery ----------
    def find(self) -> tuple[str, str]:
        self.step("2. Finding the boards on Wi-Fi")
        cam = self.a.cam or self.cfg["devices"]["cam"]
        mic = self.a.mic or self.cfg["devices"]["mic"]
        if not cam or not mic:
            found = discover(3.0, [x.split("//")[-1].split(":")[0] for x in (cam, mic) if x])
            if not cam and "cam" in found:
                cam = f"http://{found['cam']['ip']}:{found['cam'].get('port', 80)}"
            if not mic and "mic" in found:
                mic = f"{found['mic']['ip']}:{found['mic'].get('port', 5001)}"
            for role in ("cam", "mic"):
                if role in found:
                    f = found[role]
                    self.r.add(f"discovery {role}", "PASS", f"{f.get('id')} at {f.get('ip')} fw {f.get('fw')}")
                elif self.wants(role):
                    self.r.add(f"discovery {role}", "WARN" if (cam if role == 'cam' else mic) else "FAIL",
                               "no reply to discovery",
                               "Board powered? Same 2.4 GHz Wi-Fi as the laptop? Check its Serial Monitor for the IP "
                               "and pass it with --cam / --mic. Windows: allow Python on Private networks.")
        return cam, mic

    def wants(self, role: str) -> bool:
        return not self.a.only or role in self.a.only

    # ---------- camera ----------
    def camera(self, addr: str) -> None:
        self.step("3. Camera board")
        if not addr:
            self.r.add("camera", "SKIP", "camera board not found")
            return
        self.cam = CameraClient(addr, 20)
        try:
            st = self.cam.status()
        except CameraError as e:
            self.r.add("camera status", "FAIL", str(e), "Is the IP right? Open it in a browser.")
            return
        self.r.add("camera status", "PASS", f"{st.get('id')} fw {st.get('fw')} rssi {st.get('rssi')} dBm")
        self.r.add("camera sensor", "PASS" if st.get("camera_ok") else "FAIL",
                   f"camera_ok={st.get('camera_ok')} err={st.get('camera_err')}",
                   "Power off, reseat the camera ribbon (latch down), power on.")
        self.r.add("PSRAM", "PASS" if st.get("psram", 0) > 0 else "WARN", f"{st.get('psram', 0)} bytes",
                   "Without PSRAM photos are limited to SVGA. Select board 'AI Thinker ESP32-CAM'.")
        rssi = st.get("rssi", 0)
        if rssi and rssi < -75:
            self.r.add("Wi-Fi signal (cam)", "WARN", f"{rssi} dBm", "Move the hotspot closer (aim for better than -70).")
        if not st.get("camera_ok"):
            return
        ok = self.cam.apply_controls(self.cfg["camera"]["controls"])
        if ok:
            self.r.add("camera settings", "PASS" if all(ok.values()) else "WARN", str(ok))
        for size, expect in (("VGA", "640x480"), ("UXGA", "1600x1200")):
            try:
                p = self.cam.capture(size=size, quality=self.cfg["camera"]["quality"])
                (OUT / f"cam_{size}.jpg").write_bytes(p.jpeg)
                good = p.resolution == expect or p.resolution in ("?", "") or "sim" in str(st.get("fw"))
                self.r.add(f"capture {size}", "PASS" if good else "WARN",
                           f"{p.resolution}, {len(p.jpeg) // 1024} KB, {p.seconds:.2f}s → {OUT / f'cam_{size}.jpg'}")
            except CameraError as e:
                self.r.add(f"capture {size}", "FAIL", str(e), "Check power: use 5V, a good cable, no USB hub.")
        if self.a.auto:
            self.r.add("flash LED", "SKIP", "needs a person to look")
        else:
            self.ask("Look at the camera board. Press Enter, the white flash should light briefly...")
            try:
                self.cam.capture(size="VGA", flash=120, skip=1)
                seen = self.yes("Did the white flash LED light up?")
                self.r.add("flash LED", "PASS" if seen else "WARN", "seen" if seen else "not seen", "Optional; GPIO4 LED.")
            except CameraError as e:
                self.r.add("flash LED", "FAIL", str(e))
        n, fails, t0 = self.a.captures, 0, time.time()
        print(f"  … {n} full-size captures in a row (stress test)")
        for _ in range(n):
            try:
                self.cam.capture(size="UXGA", quality=self.cfg["camera"]["quality"], retries=0)
            except CameraError:
                fails += 1
        avg = (time.time() - t0) / max(n, 1)
        self.r.add("capture reliability", "PASS" if fails == 0 else ("WARN" if fails <= 1 else "FAIL"),
                   f"{n - fails}/{n} OK, {avg:.2f}s each",
                   "Failures usually mean weak power (brownout) or weak Wi-Fi.")

    def ocr(self) -> None:
        self.step("4. Can the camera read a page?")
        if not self.cam:
            self.r.add("page OCR", "SKIP", "no camera")
            return
        from visionvoice.vision.ocr import decode_image, run_ocr
        from visionvoice.vision.page_number import detect_page_number

        self.ask("Place a book page under the camera (page number visible, one page only), then press Enter...")
        c = self.cfg["camera"]
        try:
            p = self.cam.capture(size=c["size"], quality=c["quality"], flash=c["flash"], skip=c["skip"])
        except CameraError as e:
            self.r.add("page OCR", "FAIL", str(e))
            return
        (OUT / "page_test.jpg").write_bytes(p.jpeg)
        o = run_ocr(decode_image(p.jpeg, c["rotate"]), self.cfg["ocr"]["lang"])
        det = detect_page_number(o, self.cfg["ocr"]["band"], self.cfg["ocr"]["min_conf"], self.cfg["ocr"]["lang"])
        (OUT / "page_test.txt").write_text(o.text, encoding="utf-8")
        words = len(o.text.split())
        print(f"  OCR read {words} words, mean confidence {o.mean_conf:.0f}. First line: {o.text.splitlines()[0][:80] if o.text else '(none)'}")
        self.r.add("OCR text", "PASS" if words >= 40 and o.mean_conf >= 60 else "WARN",
                   f"{words} words, confidence {o.mean_conf:.0f} → {OUT / 'page_test.txt'}",
                   "Improve light, distance 25-35 cm, page flat and filling the frame, check rotate/vflip in config.toml.")
        typed = self.ask("Which page number did you place? (Enter to skip)")
        if typed.isdigit():
            ok = det.status == "DETECTED" and det.page == int(typed)
            self.r.add("page number", "PASS" if ok else "FAIL",
                       f"expected {typed}, got {det.status} {det.page} ({det.region})",
                       "Make sure the page number is inside the photo; see hwtest_output/page_test.jpg.")
        else:
            self.r.add("page number", "PASS" if det.status == "DETECTED" else "WARN", f"{det.status} {det.page} {det.region}")

    # ---------- mic ----------
    def mic_tests(self, addr: str) -> None:
        self.step("5. Microphone board")
        if not addr:
            self.r.add("mic", "SKIP", "mic board not found")
            return
        host, _, port = addr.partition(":")
        self.mic = MicClient(host, int(port or 5001), self.cfg["mic"]).start()
        if not self.mic.wait_connected(8):
            self.r.add("mic connection", "FAIL", f"could not connect to {addr}",
                       "Check the board's Serial Monitor shows '[ready] mic server'. Same Wi-Fi?")
            return
        h = self.mic.hello
        self.r.add("mic connection", "PASS", f"{h.get('id')} fw {h.get('fw')} {h.get('rate')} Hz")
        statuses = self._collect("status", 5.0)
        self.r.add("mic heartbeat", "PASS" if len(statuses) >= 3 else "FAIL", f"{len(statuses)} status messages in 5 s",
                   "Weak Wi-Fi or the board is restarting (watch Serial Monitor).")
        rssi = statuses[-1].data.get("rssi", 0) if statuses else 0
        if rssi and rssi < -75:
            self.r.add("Wi-Fi signal (mic)", "WARN", f"{rssi} dBm", "Move the hotspot closer.")

        # levels
        self.ask("Stay QUIET for 3 seconds. Press Enter to start...")
        quiet = self._levels(3)
        self.ask("Now TALK LOUDLY or clap near the mic for 3 seconds. Press Enter to start...")
        loud = self._levels(3)
        print(f"  quiet peak L={quiet[0]} R={quiet[1]}   loud peak L={loud[0]} R={loud[1]}")
        diff_l, diff_r = loud[0] - quiet[0], loud[1] - quiet[1]
        active = "L" if diff_l >= diff_r else "R"
        best = max(diff_l, diff_r)
        if self.a.auto:
            self.r.add("mic hears sound", "PASS" if max(loud) > 0 else "FAIL", f"peaks {loud}")
        else:
            self.r.add("mic hears sound", "PASS" if best > 500 else "FAIL", f"level change {best} on channel {active}",
                       "Flat = check SD wire, L/R→GND, solder joints. Garbage = SCK/WS swapped.")
            if best > 500 and active != self.cfg["mic"]["channel"]:
                self.r.add("mic channel", "WARN", f"sound is on channel {active}, config says {self.cfg['mic']['channel']}",
                           f"Set channel = \"{active}\" under [mic] in config.toml (no reflash needed).")

        # button + recording
        print("  Hold the BOOT button (or your push button), say clearly: 'read page twelve', then release.")
        if not self.a.auto:
            print("  (waiting up to 30 s)")
        ev = self._wait_utterance(30)
        if not ev:
            self.r.add("push-to-talk", "FAIL", "no recording received",
                       "Press BOOT/GPIO27 button; LED should be solid while held.")
            return
        downs = ev["downs"]
        u = ev["utt"]
        from visionvoice.audio.wav import save_wav, stats

        st = stats(u.pcm)
        wav = save_wav(OUT / "mic_test.wav", u.pcm)
        held = u.data.get("held_ms", 0)
        self.r.add("button", "PASS" if downs and held else ("PASS" if u.data.get("reason") == "host" else "FAIL"),
                   f"press detected, held {held} ms")
        self.r.add("recording", "PASS" if st["seconds"] > 0.3 and u.data.get("drops", 0) == 0 else "WARN",
                   f"{st['seconds']:.1f}s, drops {u.data.get('drops', 0)} → {wav}")
        level = "PASS"
        hint = ""
        if st["rms"] < 300:
            level, hint = "WARN", "Too quiet: lower gain_shift (e.g. 13 or 12) in config.toml [mic], or speak closer."
        if st["clipped"] > 0.01:
            level, hint = "WARN", "Too loud / distorted: raise gain_shift (e.g. 15) in config.toml [mic]."
        self.r.add("recording level", level, f"rms {st['rms']:.0f}, peak {st['peak']}, clipped {st['clipped']*100:.1f}%", hint)
        self._play(wav)
        heard = self.yes("Did you hear your own voice clearly from the laptop?")
        if heard is not None:
            self.r.add("playback check", "PASS" if heard else "WARN", "clear" if heard else "not clear",
                       "Check gain_shift and mic placement; the mic's sound hole is on the module.")
        self._stt(u.pcm)

        # LED
        if self.a.auto:
            self.r.add("mic LED", "SKIP", "needs a person to look")
        else:
            print("  Watch the blue LED: ON for 2 s, FAST BLINK for 2 s, then OFF for 2 s.")
            for mode in (1, 3, 0):
                self.mic.set_led(mode)
                time.sleep(2)
            self.mic.set_led(4)
            seen = self.yes("Did the LED do ON → fast blink → OFF?")
            self.r.add("mic LED", "PASS" if seen else "WARN", "ok" if seen else "not seen",
                       "Some boards have no LED on GPIO2; this is optional.")

    def _stt(self, pcm: bytes) -> None:
        from visionvoice.audio.stt import make_stt
        from visionvoice.logic.commands import parse_command

        stt = make_stt(self.cfg["stt"])
        if not getattr(stt, "available", False):
            self.r.add("speech recognition", "WARN", "faster-whisper not installed", "pip install faster-whisper")
            return
        print("  … transcribing (the first time downloads the model, ~150 MB)")
        t0 = time.time()
        tr = stt.transcribe(pcm)
        cmd = parse_command(tr.text) if tr.text else None
        ok = cmd is not None and cmd.intent == "READ_PAGES" and cmd.pages == (12, 12)
        self.r.add("speech recognition", "PASS" if ok else "WARN",
                   f'heard "{tr.text}" ({tr.status}, {time.time() - t0:.1f}s) → {cmd.intent if cmd else "-"} {cmd.pages if cmd else ""}',
                   "If the words are wrong: speak after the LED turns on, 10-20 cm from the mic, and check the level above.")

    def _collect(self, kind: str, seconds: float) -> list:
        out, end = [], time.time() + seconds
        while time.time() < end:
            try:
                ev = self.mic.events.get(timeout=0.2)
            except queue.Empty:
                continue
            if ev.kind == kind:
                out.append(ev)
        return out

    def _levels(self, seconds: float) -> tuple[int, int]:
        l = r = 0
        for ev in self._collect("status", seconds):
            lv = ev.data.get("lvl", [0, 0])
            l, r = max(l, int(lv[0])), max(r, int(lv[1]))
        return l, r

    def _wait_utterance(self, timeout: float):
        if self.a.auto:
            self.mic.request_stream(True)
            threading_timer(1.5, lambda: self.mic.request_stream(False))
        downs, end = [], time.time() + timeout
        while time.time() < end:
            try:
                ev = self.mic.events.get(timeout=0.3)
            except queue.Empty:
                continue
            if ev.kind == "button_down":
                downs.append(ev)
                print("  … button down, recording")
            elif ev.kind == "utterance":
                return {"downs": downs, "utt": ev}
        return None

    def _play(self, wav: Path) -> None:
        if self.a.auto:
            return
        try:
            if sys.platform.startswith("win"):
                import winsound

                winsound.PlaySound(str(wav), winsound.SND_FILENAME)
            elif sys.platform == "darwin":
                subprocess.run(["afplay", str(wav)], check=False)
            elif shutil.which("aplay"):
                subprocess.run(["aplay", "-q", str(wav)], check=False)
        except Exception as e:
            print(f"  (could not play audio: {e})")

    # ---------- long tests ----------
    def reconnect(self) -> None:
        self.step("6. Recovery test")
        if self.mic:
            self.ask("UNPLUG the mic board's USB for 5 seconds, plug it back in, then press Enter...")
            ok = self.mic.wait_connected(0)
            t_end = time.time() + 60
            saw_drop = False
            while time.time() < t_end:
                try:
                    ev = self.mic.events.get(timeout=0.5)
                except queue.Empty:
                    continue
                if ev.kind == "disconnected":
                    saw_drop = True
                if ev.kind == "connected":
                    ok = True
                    break
            self.r.add("mic reconnects by itself", "PASS" if ok else "FAIL",
                       "reconnected" + ("" if saw_drop else " (drop not seen)"), "Board must rejoin Wi-Fi; check config.h networks.")
        if self.cam:
            self.ask("UNPLUG the camera board for 5 seconds, plug it back in, then press Enter...")
            ok = False
            for _ in range(30):
                try:
                    if self.cam.status().get("camera_ok"):
                        ok = True
                        break
                except CameraError:
                    pass
                time.sleep(2)
            self.r.add("camera recovers by itself", "PASS" if ok else "FAIL", "back online" if ok else "not back after 60 s")

    def soak(self, minutes: float) -> None:
        self.step(f"7. Stability run ({minutes:g} min)")
        end = time.time() + minutes * 60
        caps = cfail = 0
        drops = 0
        while time.time() < end:
            if self.cam:
                try:
                    self.cam.capture(size="UXGA", retries=0)
                    caps += 1
                except CameraError:
                    cfail += 1
            if self.mic:
                for ev in self._collect("disconnected", 0.1):
                    drops += 1
            left = int(end - time.time())
            print(f"\r  captures ok {caps} failed {cfail} | mic disconnects {drops} | {left}s left   ", end="", flush=True)
            time.sleep(8)
        print()
        if self.cam:
            self.r.add("camera stability", "PASS" if cfail == 0 else "FAIL", f"{caps} ok, {cfail} failed")
        if self.mic:
            self.r.add("mic stability", "PASS" if drops == 0 and self.mic.connected.is_set() else "FAIL",
                       f"{drops} disconnects", "Weak Wi-Fi or unstable power.")

    def run(self) -> int:
        self.env()
        cam, mic = self.find()
        if self.wants("cam"):
            self.camera(cam)
            self.ocr()
        if self.wants("mic"):
            self.mic_tests(mic)
        if self.a.reconnect:
            self.reconnect()
        if self.a.soak:
            self.soak(self.a.soak)
        if self.mic:
            self.mic.close()
        text = self.r.text()
        (OUT / "report.txt").write_text(text, encoding="utf-8")
        print("\n" + "=" * 60 + "\n" + text + f"\n\nSaved to {OUT / 'report.txt'}")
        return 1 if self.r.failed else 0


def threading_timer(sec: float, fn) -> None:
    import threading

    t = threading.Timer(sec, fn)
    t.daemon = True
    t.start()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--cam", default="", help="camera address, e.g. http://192.168.43.50")
    ap.add_argument("--mic", default="", help="mic address, e.g. 192.168.43.51:5001")
    ap.add_argument("--only", choices=["cam", "mic"])
    ap.add_argument("--captures", type=int, default=10)
    ap.add_argument("--soak", type=float, default=0, help="minutes of stability testing")
    ap.add_argument("--reconnect", action="store_true")
    ap.add_argument("--auto", action="store_true", help="no questions (simulator / CI)")
    a = ap.parse_args()
    sys.exit(Tester(a, load_config(a.config)).run())


if __name__ == "__main__":
    main()
