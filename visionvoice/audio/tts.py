"""Text-to-speech through the laptop speakers. Interruptible (stop() ends speech at once)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading


class Speaker:
    def __init__(self, engine: str = "auto", rate: int = 0, echo: bool = True) -> None:
        self.rate = rate
        self.echo = echo
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.engine = self._pick(engine)
        self.last = ""

    def _pick(self, engine: str) -> str:
        if engine not in ("auto", ""):
            return engine
        if sys.platform.startswith("win"):
            return "windows"
        if sys.platform == "darwin":
            return "say"
        for exe in ("espeak-ng", "espeak", "spd-say"):
            if shutil.which(exe):
                return exe
        return "print"

    def speak(self, text: str, block: bool = False) -> None:
        self.stop()
        self.last = text
        if self.echo:
            print(f"\n  🔊 {text}\n", flush=True)
        if self.engine == "print":
            return
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if self.engine == "windows":
            ps = (
                "Add-Type -AssemblyName System.Speech;"
                "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                f"$s.Rate={int(self.rate)};"
                f"$s.Speak([IO.File]::ReadAllText('{path}'))"
            )
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        elif self.engine == "say":
            cmd, flags = ["say", "-f", path], 0
        elif self.engine in ("espeak-ng", "espeak"):
            cmd, flags = [self.engine, "-f", path], 0
        else:
            cmd, flags = ["spd-say", "-w", text], 0
        with self._lock:
            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            except OSError:
                self.engine = "print"
                return
        threading.Thread(target=self._cleanup, args=(self._proc, path), daemon=True).start()
        if block:
            self.wait()

    def _cleanup(self, proc: subprocess.Popen, path: str) -> None:
        proc.wait()
        try:
            os.remove(path)
        except OSError:
            pass

    def stop(self) -> None:
        with self._lock:
            p, self._proc = self._proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()

    def is_speaking(self) -> bool:
        p = self._proc
        return bool(p and p.poll() is None)

    def wait(self) -> None:
        p = self._proc
        if p:
            p.wait()
