# Configuration

Fox reads its settings from `.env` in the project folder (or `%LOCALAPPDATA%\Fox\.env` for a copied `Fox.exe`). Start from `.env.example`. Most settings can also be changed in the desktop app under **Settings**, which writes them to `.env` and applies them without a restart.

Names are case-insensitive. Environment variables with the same names override `.env`.

> **Keep `.env` private.** API keys and your email password are stored in it as plain text. It's in `.gitignore`; never commit it.

## Models

| Setting | Default | Meaning |
|---|---|---|
| `FOX_MODEL_CHAIN` | `azure,gemini,groq,openai,anthropic,ollama` | Providers to try, left to right. Each entry is `provider` or `provider:model`. Providers without credentials are skipped. |
| `FOX_MODEL_TIMEOUT` | `60` | Seconds to wait for a model before falling back. |
| `FOX_MODEL_COOLDOWN` | `60` | Seconds a failed model is skipped. A rejected key is skipped for 5 minutes. |
| `FOX_TEMPERATURE` | `0.3` | Sampling temperature. |
| `FOX_MAX_CONTEXT_TOKENS` | `24000` | History kept per request. Older messages are dropped, whole turns at a time. |
| `FOX_MAX_STEPS` | `15` | Most model/tool rounds in one turn. |

Example: try Groq's Llama first, then Gemini, then a local model.

```ini
FOX_MODEL_CHAIN=groq:llama-3.3-70b-versatile,gemini,ollama
```

### Providers

| Provider | Settings | Get a key |
|---|---|---|
| `azure` | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_CHAT_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION` | <https://portal.azure.com> |
| `openai` | `OPENAI_API_KEY`, `OPENAI_MODEL` (`gpt-4.1`), `OPENAI_BASE_URL` for compatible servers | <https://platform.openai.com/api-keys> |
| `anthropic` | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (`claude-sonnet-5-5`) | <https://console.anthropic.com/settings/keys> |
| `gemini` | `GOOGLE_API_KEY`, `GEMINI_MODEL` (`gemini-2.5-flash`) | <https://aistudio.google.com/apikey> |
| `groq` | `GROQ_API_KEY`, `GROQ_MODEL` (`llama-3.3-70b-versatile`) | <https://console.groq.com/keys> |
| `ollama` | `OLLAMA_MODEL` (required), `OLLAMA_BASE_URL` (`http://localhost:11434`) | No key. Install from <https://ollama.com> |

The model must support **tool calling**. Small local models often call tools poorly; see [Limitations](limitations.md#models).

## Permissions and tools

| Setting | Default | Meaning |
|---|---|---|
| `FOX_PERMISSION_TIMEOUT` | `120` | Seconds a prompt waits. No answer means no. |
| `FOX_TOOL_TIMEOUT` | `120` | Default seconds a tool may run. Plugins can set their own. |
| `FOX_MAX_TOOL_OUTPUT` | `20000` | Characters of tool output passed to the model. The rest is cut off. |

## Email

| Setting | Default |
|---|---|
| `EMAIL_ADDRESS` | |
| `EMAIL_PASSWORD` | An app password, not your normal password |
| `SMTP_HOST`, `SMTP_PORT` | `smtp.gmail.com`, `587` |
| `IMAP_HOST`, `IMAP_PORT` | `imap.gmail.com`, `993` |

## Voice output

`FOX_TTS_CHAIN` lists engines to try, left to right. Keep `pyttsx3` last as an offline fallback.

| Engine | Needs | Settings |
|---|---|---|
| `pyttsx3` | Nothing; offline, built-in Windows voice | `FOX_TTS_RATE` (`170`) |
| `edge` | Internet; natural Microsoft voices, no key | `FOX_EDGE_VOICE` (`en-US-GuyNeural`) |
| `elevenlabs` | `ELEVENLABS_API_KEY` | `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL` |
| `openai` | `OPENAI_API_KEY` | `OPENAI_TTS_MODEL` (`gpt-4o-mini-tts`), `OPENAI_TTS_VOICE` (`onyx`) |
| `azure` | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | `AZURE_SPEECH_VOICE` |

`FOX_TTS_ROBOT_EFFECT=true` (the default) adds a metallic effect to any voice. Set it to `false` for a clean voice.

## Voice input

`FOX_STT_CHAIN` lists speech-recognition engines, left to right.

| Engine | Needs | Settings |
|---|---|---|
| `faster_whisper` | Nothing; offline. Downloads a model on first use | `FOX_WHISPER_MODEL` (`base.en`) |
| `groq` | `GROQ_API_KEY` | `GROQ_STT_MODEL` (`whisper-large-v3-turbo`) |
| `openai` | `OPENAI_API_KEY` | `OPENAI_STT_MODEL` (`gpt-4o-mini-transcribe`) |

`FOX_WAKE_WORD` (`fox`) is the word hands-free mode listens for.

## Local API

| Setting | Default | Meaning |
|---|---|---|
| `FOX_SERVER_PORT` | `8765` | Port for `fox serve`. The desktop app picks its own free port. |
| `FOX_ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Web pages allowed to call the API from a browser. |

## Paths

| Setting | Default |
|---|---|
| `FOX_DATA_DIR` | `data` |
| `FOX_AGENTS_DIR` | `agents` |
| `FOX_PLUGINS_DIR` | `plugins` |

The defaults are inside the folder that holds `.env`. Use absolute paths when you override them. The test suite points these at a temporary folder so it never touches your data.
