"""Edit .env from the GUI. Keeps comments and order, and never sends a saved secret back out."""

from __future__ import annotations

import os
import re
from pathlib import Path

SECRET_KEYS = {
    "AZURE_OPENAI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "EMAIL_PASSWORD", "ELEVENLABS_API_KEY", "AZURE_SPEECH_KEY",
}  # fmt: skip
PLAIN_KEYS = {
    "FOX_MODEL_CHAIN", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_CHAT_DEPLOYMENT",
    "GEMINI_MODEL", "GROQ_MODEL", "OPENAI_MODEL", "OPENAI_BASE_URL", "ANTHROPIC_MODEL",
    "OLLAMA_BASE_URL", "OLLAMA_MODEL",
    "EMAIL_ADDRESS", "SMTP_HOST", "SMTP_PORT", "IMAP_HOST", "IMAP_PORT",
    "FOX_TTS_CHAIN", "FOX_TTS_ROBOT_EFFECT", "FOX_EDGE_VOICE", "AZURE_SPEECH_REGION",
    "FOX_STT_CHAIN", "FOX_WHISPER_MODEL", "FOX_WAKE_WORD",
    # Advanced behaviour. Numbers and timeouts; see fox/config.py for what each one does.
    "FOX_MAX_STEPS", "FOX_TEMPERATURE", "FOX_MAX_CONTEXT_TOKENS", "FOX_MODEL_TIMEOUT", "FOX_MODEL_COOLDOWN",
    "FOX_PERMISSION_TIMEOUT", "FOX_TOOL_TIMEOUT", "FOX_MAX_TOOL_OUTPUT",
}  # fmt: skip
EDITABLE_KEYS = SECRET_KEYS | PLAIN_KEYS


def update_env(path: Path, values: dict[str, str]) -> None:
    """Set keys in the file, replacing their lines or adding them at the end."""
    bad = [key for key in values if key not in EDITABLE_KEYS]
    if bad:
        raise ValueError(f"These settings can't be changed here: {', '.join(sorted(bad))}")
    if any(re.search(r"[\r\n]", value) for value in values.values()):
        raise ValueError("Settings must be a single line.")

    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    for index, line in enumerate(lines):
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            lines[index] = f"{key}={remaining.pop(key)}"
    lines.extend(f"{key}={value}" for key, value in remaining.items())

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
