# Getting started

This guide takes you from a fresh clone to your first conversation with Fox.

## Requirements

| Requirement | Notes |
|---|---|
| Windows 11 | Fox controls Windows apps, windows, audio and power settings. Windows 10 may work but isn't tested. Other operating systems aren't supported. |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | Installs Python 3.11–3.13 and every dependency for you. |
| A model | An API key for Azure OpenAI, OpenAI, Anthropic, Google Gemini or Groq, or a local model through [Ollama](https://ollama.com). |
| Microsoft Edge WebView2 Runtime | Only for the desktop app. It comes with Windows 11. |
| A microphone and speakers | Only for voice. |

## 1. Get the code

```powershell
git clone https://github.com/Agni-glitch/fox.git
cd fox
```

## 2. Install

```powershell
uv sync --all-extras
```

This creates `.venv` and installs everything, including all model providers, voice and the desktop app. To install less, pick extras instead of `--all-extras`:

| Extra | Adds |
|---|---|
| `gemini` | Google Gemini |
| `groq` | Groq |
| `anthropic` | Anthropic Claude |
| `ollama` | Local models through Ollama |
| `voice` | Microphone input, speech recognition and text-to-speech |
| `gui` | The desktop app |

Azure OpenAI and OpenAI are always included. For example: `uv sync --extra gui --extra groq`.

## 3. Add a model

```powershell
copy .env.example .env
notepad .env
```

Fill in at least one provider. Gemini and Groq both have free tiers and are the quickest way to try Fox:

```ini
GOOGLE_API_KEY=your-key
FOX_MODEL_CHAIN=gemini,groq
```

Providers in `FOX_MODEL_CHAIN` with no key are skipped. You can also add keys later in the desktop app on the **Models** page, where you can add other models too ([Models](models.md)). Every setting is described in [Configuration](configuration.md).

## 4. Check your setup

```powershell
uv run fox doctor
```

`fox doctor` shows, for each model provider, whether its key is set and its library installed, then the fallback order it will use, and your email and voice settings.

## 5. Start Fox

```powershell
uv run fox gui     # the desktop app
uv run fox chat    # or chat in the terminal
uv run fox voice   # or talk: press Enter, then speak
```

Try a safe request first, such as *"What's my battery level?"*, then one that asks permission, such as *"Open Notepad"*. You'll see a prompt with **Allow once**, **Allow this session**, **Always allow**, **Deny** and **Never allow**. Read [Security model](security-model.md) to understand what each one means.

Tip: put `uv run` in front of every `fox` command, or activate the environment once with `.venv\Scripts\activate`.

## 6. Optional: build the desktop app

```powershell
.\build.bat
```

This runs the tests and creates `Fox.exe` and the shortcut `Fox.lnk` in the project folder. See [Building Fox.exe](building.md).

## Where your data lives

Running from source, Fox keeps everything inside the project folder:

| Path | Contents | Keep private? |
|---|---|---|
| `.env` | API keys, email password and settings, in plain text | **Yes** |
| `data/fox.db` | Conversations, saved facts, reminders, the permission log | **Yes** |
| `data/permissions.json` | Remembered "always" and "never" answers | Yes |
| `data/control.json` | The kill switch and turned-off tools | Yes |
| `data/api_token.txt` | The token for the local API | **Yes** |
| `data/fox.log` | The log, rotated at 2 MB | Yes |
| `agents/` | Your specialist agents, as YAML | No |
| `plugins/` | Your plugin tools, as Python | No |

`.env` and `data/` are listed in `.gitignore`, so they aren't committed. A copy of `Fox.exe` placed outside the project folder uses `%LOCALAPPDATA%\Fox` instead. See [Building Fox.exe](building.md#where-the-app-keeps-its-data).

## Next steps

- [User guide](user-guide.md): every page of the app, voice and terminal commands.
- [Use cases](use-cases.md): ideas for what to ask.
- [Writing plugins](plugins.md): give Fox a new ability.
- [Creating agents](agents.md): add a specialist.
