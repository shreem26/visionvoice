"""Speech-to-text. Local faster-whisper by default; swap engines here later."""
from __future__ import annotations

import threading
from dataclasses import dataclass

from .wav import stats, to_float

PROMPT = "read page, read pages 12 to 15, capture, next, summarize, explain, repeat, stop, cancel, status, help"
SILENCE_RMS = 120  # int16 RMS below this = nobody spoke


@dataclass
class Transcription:
    status: str  # OK | NO_SPEECH | ERROR
    text: str = ""
    error: str = ""


class NullSTT:
    name = "none"
    available = False

    def transcribe(self, pcm: bytes) -> Transcription:
        return Transcription("ERROR", error="speech recognition is not installed")


class FasterWhisperSTT:
    name = "faster_whisper"

    def __init__(self, model: str = "base.en", device: str = "cpu", compute_type: str = "int8") -> None:
        from faster_whisper import WhisperModel  # noqa: F401  (import check)

        self.model_name, self.device, self.compute_type = model, device, compute_type
        self._model = None
        self._lock = threading.Lock()
        self.available = True

    def load(self) -> None:
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)

    def transcribe(self, pcm: bytes) -> Transcription:
        st = stats(pcm)
        if st["seconds"] < 0.3 or st["rms"] < SILENCE_RMS:
            return Transcription("NO_SPEECH")
        try:
            self.load()
            segments, _ = self._model.transcribe(
                to_float(pcm), language="en", beam_size=3, vad_filter=True, initial_prompt=PROMPT,
                condition_on_previous_text=False,
            )
            segs = [s for s in segments if s.no_speech_prob < 0.8]
            text = " ".join(s.text.strip() for s in segs).strip()
        except Exception as e:
            return Transcription("ERROR", error=str(e))
        return Transcription("OK", text) if text else Transcription("NO_SPEECH")


def make_stt(cfg: dict):
    if cfg.get("engine") == "faster_whisper":
        try:
            return FasterWhisperSTT(cfg.get("model", "base.en"), cfg.get("device", "cpu"), cfg.get("compute_type", "int8"))
        except ImportError:
            return NullSTT()
    return NullSTT()
