"""Microphone recording and speaker playback."""

from __future__ import annotations

import io
import threading
import wave

import numpy as np

SAMPLE_RATE = 16000
FRAME_SECONDS = 0.03


def play(samples: np.ndarray, sample_rate: int) -> None:
    import sounddevice as sd

    sd.play(samples, sample_rate)
    sd.wait()


def stop_playback() -> None:
    import sounddevice as sd

    sd.stop()


def record_utterance(
    start_timeout: float = 8.0,
    max_seconds: float = 30.0,
    silence_seconds: float = 0.9,
    stop: threading.Event | None = None,
) -> np.ndarray | None:
    """Record one spoken phrase. Returns None if nobody starts speaking before the timeout.

    The first half second sets the noise floor, so this adapts to quiet and noisy rooms.
    Setting `stop` from another thread releases the microphone straight away.
    """
    import sounddevice as sd

    frame = int(SAMPLE_RATE * FRAME_SECONDS)
    captured: list[np.ndarray] = []
    pre_roll: list[np.ndarray] = []
    noise = []
    threshold = 0.0
    silent = 0.0
    waited = 0.0
    speaking = False

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=frame) as stream:
        while True:
            if stop is not None and stop.is_set():
                return None
            block = stream.read(frame)[0][:, 0].copy()
            level = float(np.sqrt(np.mean(block**2)))

            if len(noise) < int(0.5 / FRAME_SECONDS):
                noise.append(level)
                threshold = max(0.012, float(np.mean(noise)) * 3)
                continue

            if not speaking:
                pre_roll = [*pre_roll[-10:], block]  # keeps the start of the first word
                waited += FRAME_SECONDS
                if level > threshold:
                    speaking = True
                    captured = list(pre_roll)
                elif waited > start_timeout:
                    return None
                continue

            captured.append(block)
            silent = silent + FRAME_SECONDS if level < threshold else 0.0
            if silent >= silence_seconds or len(captured) * FRAME_SECONDS >= max_seconds:
                break

    audio = np.concatenate(captured)
    # A click or cough isn't speech.
    return audio if len(audio) / SAMPLE_RATE > silence_seconds + 0.25 else None


def to_wav_bytes(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(pcm.tobytes())
    return buffer.getvalue()


def from_wav_bytes(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as source:
        rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
        raw = source.readframes(source.getnframes())
    if width != 2:
        raise ValueError(f"Unsupported WAV sample width: {width}")
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def decode_compressed(data: bytes) -> tuple[np.ndarray, int]:
    """Decode the MP3 returned by cloud voices, with no need for ffmpeg."""
    import miniaudio

    decoded = miniaudio.decode(data, output_format=miniaudio.SampleFormat.FLOAT32, nchannels=1)
    return np.frombuffer(decoded.samples, dtype=np.float32).copy(), decoded.sample_rate
