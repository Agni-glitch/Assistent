"""Settings, loaded from .env at the project root."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def find_root() -> Path:
    """The folder holding .env, data, agents and plugins.

    Fox.exe in the project's root folder shares the project's folders, so the app and the CLI see
    the same chats and permissions. Copied anywhere else, it uses %LOCALAPPDATA%\\Fox.
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[1]
    project = Path(sys.executable).resolve().parent
    if (project / "pyproject.toml").is_file() and (project / "fox" / "__init__.py").is_file():
        return project
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Fox"


ROOT = find_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # Model fallback: tried left to right. Entries are "provider" or "provider:model".
    fox_model_chain: str = "azure,gemini,groq,openai,anthropic,ollama"
    fox_model_timeout: float = 60
    fox_model_cooldown: float = 60
    fox_temperature: float = 0.3
    fox_max_context_tokens: int = 24000
    fox_max_steps: int = 15

    # Azure OpenAI
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-12-01-preview"
    azure_openai_chat_deployment: str = ""

    # Other providers
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = "gpt-4.1"
    google_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = ""

    # Permissions and tools
    fox_permission_timeout: float = 120
    fox_tool_timeout: float = 120
    fox_max_tool_output: int = 20000

    # Email
    email_address: str = ""
    email_password: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993

    # Voice output: tried left to right, so the last entry should work offline.
    fox_tts_chain: str = "pyttsx3"
    fox_tts_robot_effect: bool = True
    fox_tts_rate: int = 170
    fox_edge_voice: str = "en-US-GuyNeural"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"
    elevenlabs_model: str = "eleven_turbo_v2_5"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "onyx"
    azure_speech_key: str = ""
    azure_speech_region: str = ""
    azure_speech_voice: str = "en-US-GuyNeural"

    # Voice input
    fox_stt_chain: str = "faster_whisper"
    fox_whisper_model: str = "base.en"
    groq_stt_model: str = "whisper-large-v3-turbo"
    openai_stt_model: str = "gpt-4o-mini-transcribe"
    fox_wake_word: str = "fox"

    # Local API server. The desktop app runs its own copy on a free port, so this is for `fox serve`.
    fox_server_port: int = 8765
    fox_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Paths
    fox_data_dir: Path = ROOT / "data"
    fox_agents_dir: Path = ROOT / "agents"
    fox_plugins_dir: Path = ROOT / "plugins"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.fox_data_dir.mkdir(parents=True, exist_ok=True)
    return settings
