"""Speech to text, with fallback between local Whisper and cloud services."""

from __future__ import annotations

import logging
import os

import httpx
import numpy as np

from fox.config import Settings
from fox.voice.audio import to_wav_bytes

log = logging.getLogger("fox.voice")


class Transcriber:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.chain = [name.strip() for name in settings.fox_stt_chain.split(",") if name.strip()]
        self._whisper = None

    def _faster_whisper(self, audio: np.ndarray) -> str:
        if self._whisper is None:
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            from faster_whisper import WhisperModel

            # The model downloads on first use, then runs offline.
            self._whisper = WhisperModel(self.settings.fox_whisper_model, device="cpu", compute_type="int8")
        segments, _ = self._whisper.transcribe(audio, beam_size=1, vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments)

    def _cloud(self, url: str, key: str, model: str, audio: np.ndarray) -> str:
        if not key:
            raise RuntimeError(f"No API key for {url}")
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            files={"file": ("speech.wav", to_wav_bytes(audio), "audio/wav")},
            data={"model": model},
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("text", "")

    def transcribe(self, audio: np.ndarray) -> str:
        s = self.settings
        errors = []
        for name in self.chain:
            try:
                if name == "faster_whisper":
                    return self._faster_whisper(audio).strip()
                if name == "groq":
                    url = "https://api.groq.com/openai/v1/audio/transcriptions"
                    return self._cloud(url, s.groq_api_key, s.groq_stt_model, audio).strip()
                if name == "openai":
                    url = "https://api.openai.com/v1/audio/transcriptions"
                    return self._cloud(url, s.openai_api_key, s.openai_stt_model, audio).strip()
                errors.append(f"{name}: unknown speech provider")
            except Exception as exc:
                log.warning("STT provider %s failed: %s", name, exc)
                errors.append(f"{name}: {exc}")
        raise RuntimeError("No speech provider worked: " + "; ".join(errors))
