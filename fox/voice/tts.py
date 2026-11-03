"""Text to speech, with fallback between voices and an optional robot effect."""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from html import escape
from pathlib import Path
from typing import Callable

import httpx
import numpy as np

from fox.config import Settings
from fox.voice.audio import decode_compressed, from_wav_bytes, play

log = logging.getLogger("fox.voice")

Audio = tuple[np.ndarray, int]


def robotize(samples: np.ndarray, sample_rate: int, carrier_hz: float = 55.0, mix: float = 0.75) -> np.ndarray:
    """Ring modulation: the classic robot voice from film and TV, computed with no extra libraries."""
    time = np.arange(len(samples)) / sample_rate
    carrier = np.sin(2 * np.pi * carrier_hz * time).astype(np.float32)
    # A very short echo adds the metallic ring.
    delay = int(sample_rate * 0.012)
    echoed = samples.copy()
    if delay and len(samples) > delay:
        echoed[delay:] += 0.4 * samples[:-delay]
    output = (1 - mix) * samples + mix * echoed * carrier
    peak = float(np.max(np.abs(output))) if len(output) else 0
    return output / peak * 0.9 if peak > 0 else output


def _pyttsx3(s: Settings, text: str) -> Audio:
    """Offline Windows voice with no API key. Sounds robotic even before the effect."""
    import comtypes
    import pyttsx3

    comtypes.CoInitialize()  # speech runs on a worker thread
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "speech.wav"
        engine = pyttsx3.init("sapi5")
        engine.setProperty("rate", s.fox_tts_rate)
        engine.save_to_file(text, str(path))
        engine.runAndWait()
        engine.stop()
        del engine
        return from_wav_bytes(path.read_bytes())


def _edge(s: Settings, text: str) -> Audio:
    """Free natural Microsoft voices with no API key. Needs internet."""
    import edge_tts

    async def collect() -> bytes:
        data = bytearray()
        async for chunk in edge_tts.Communicate(text, s.fox_edge_voice).stream():
            if chunk["type"] == "audio":
                data.extend(chunk["data"])
        return bytes(data)

    return decode_compressed(asyncio.run(collect()))


def _elevenlabs(s: Settings, text: str) -> Audio:
    if not s.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    response = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{s.elevenlabs_voice_id}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": s.elevenlabs_api_key},
        json={"text": text, "model_id": s.elevenlabs_model},
        timeout=30,
    )
    response.raise_for_status()
    return decode_compressed(response.content)


def _openai(s: Settings, text: str) -> Audio:
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    response = httpx.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {s.openai_api_key}"},
        json={"model": s.openai_tts_model, "voice": s.openai_tts_voice, "input": text, "response_format": "mp3"},
        timeout=30,
    )
    response.raise_for_status()
    return decode_compressed(response.content)


def _azure(s: Settings, text: str) -> Audio:
    if not (s.azure_speech_key and s.azure_speech_region):
        raise RuntimeError("AZURE_SPEECH_KEY and AZURE_SPEECH_REGION are not set")
    ssml = (
        f"<speak version='1.0' xml:lang='en-US'><voice name='{escape(s.azure_speech_voice, quote=True)}'>"
        f"{escape(text)}</voice></speak>"
    )
    response = httpx.post(
        f"https://{s.azure_speech_region}.tts.speech.microsoft.com/cognitiveservices/v1",
        headers={
            "Ocp-Apim-Subscription-Key": s.azure_speech_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
        },
        content=ssml.encode("utf-8"),
        timeout=30,
    )
    response.raise_for_status()
    return from_wav_bytes(response.content)


PROVIDERS: dict[str, Callable[[Settings, str], Audio]] = {
    "pyttsx3": _pyttsx3,
    "edge": _edge,
    "elevenlabs": _elevenlabs,
    "openai": _openai,
    "azure": _azure,
}


def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " The code is on screen. ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[*_#`>|~]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class Speaker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.chain = [name.strip() for name in settings.fox_tts_chain.split(",") if name.strip()]

    def synthesize(self, text: str) -> Audio:
        errors = []
        for name in self.chain:
            provider = PROVIDERS.get(name)
            if provider is None:
                errors.append(f"{name}: unknown voice provider")
                continue
            try:
                samples, rate = provider(self.settings, text)
                if self.settings.fox_tts_robot_effect:
                    samples = robotize(samples, rate)
                return samples, rate
            except Exception as exc:
                log.warning("TTS provider %s failed: %s", name, exc)
                errors.append(f"{name}: {exc}")
        raise RuntimeError("No voice could speak: " + "; ".join(errors))

    def say(self, text: str) -> None:
        cleaned = clean_for_speech(text)
        if cleaned:
            play(*self.synthesize(cleaned))
