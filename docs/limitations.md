# Limitations and troubleshooting

Fox is alpha software. This page lists what it can't do, the trade-offs it makes on purpose, and known issues, so you can decide whether it fits your needs.

## Platform

- **Windows 11 only.** App control, audio, brightness, notifications, windows and the desktop app all use Windows APIs. Windows 10 may work but isn't tested. macOS and Linux aren't supported.
- **Python 3.11 to 3.13.**
- **Single user.** Fox acts with the access of the Windows account that runs it. It has no user accounts or roles of its own.
- **The desktop app needs WebView2**, which comes with Windows 11.

## Models

- **Quality depends on the model.** Fox's reliability is only as good as the model's tool calling. Large hosted models do well; small local models (Ollama) often pick the wrong tool, invent arguments or loop. Use a capable model for anything important.
- **The model must support tool calling.** Models without it can chat but can't act.
- **Fallback isn't free.** If a model fails mid-reply, Fox restarts the reply on the next one, which costs time and tokens.
- **Context is trimmed.** Long conversations keep only the most recent history (`FOX_MAX_CONTEXT_TOKENS`). Use **remember** for lasting facts.
- **Your text goes to your provider.** Messages, tool results (including file and email contents Fox reads) and saved facts are sent to the model you configure. For full privacy use a local model through Ollama.
- **No vision.** Fox can take a screenshot and save it, but can't look at it or at the screen, so it can't click specific buttons in other apps.

## Safety trade-offs

- **Prompt injection is reduced, not solved.** Outside content is marked as untrusted, but a model can still be manipulated. Read critical prompts carefully, especially after Fox read an email or web page. See [Security model](security-model.md#prompt-injection).
- **Approval is the real control.** Once you approve an action, Fox does it with your full access. `run_powershell` in particular is not bound by the file restrictions.
- **Plugins aren't sandboxed.** Plugin code runs with your full access. Only install plugins you've read.
- **Secrets are stored in plain text** in `.env`. Anyone or anything with access to your account can read them. Windows Credential Manager support isn't implemented yet.
- **Nothing runs unattended.** With no window or terminal to answer, every prompt is refused. This is deliberate, but it means Fox can't do scheduled work on its own.

## Features

- **Reminders fire only while Fox is running.** Missed ones show as notifications at the next start.
- **Email is SMTP/IMAP only**, with app passwords. No OAuth, Outlook/Exchange Graph API or calendar.
- **No click automation.** Fox can type text, press keys and manage windows, but not click on things.
- **Voice is English-first.** The default speech model (`base.en`) and the voice-approval words are English. Other languages need another Whisper model and may not recognise "yes"/"confirm".
- **Offline speech recognition can be slow** on computers with little memory. Groq or OpenAI speech recognition is much faster.
- **Tool output is capped** at `FOX_MAX_TOOL_OUTPUT` characters, so very large files or pages are cut off.
- **One turn at a time** per connection.
- **Attachments are read as text.** Scanned PDFs need OCR first, and spreadsheets (`.xlsx`) and slides (`.pptx`) aren't read; save them as CSV or PDF. Images need a model that can see.
- **The model picker has no fallback.** A model picked in the chat's **Model** menu answers alone; if it fails, you see the error. Choose **Auto** to fall back.
- **Browser capture is Chrome-only** as included, captures one tab at a time, and shows Chrome's debugging bar while it runs. WebSocket frames and binary bodies aren't kept. Secret removal works by header and field name, so it can miss secrets with unusual names.

## Known issues

- **The packaged app sometimes fails to open.** On some Windows builds, `Fox.exe` crashes inside Windows while creating its window, about one launch in ten (error `0xC000041D`). It has never been seen when running from source. The cause is unknown; the launcher detects it and retries automatically, which is why Task Manager shows two `Fox.exe` processes. Retries are logged in `data/fox.log`.
- **`Fox.exe` isn't code-signed,** so SmartScreen and some antivirus products may warn about it.
- **Plugin API is not yet stable.** Expect changes before 1.0.

## Troubleshooting

| Problem | Try |
|---|---|
| "No model configured" | Add a key in **Settings** or `.env`, then run `fox doctor`. Check the provider appears in `FOX_MODEL_CHAIN`. |
| A provider is always skipped | `fox doctor` shows whether its key is set and its library is installed. Install extras with `uv sync --all-extras`. |
| Fox says it can't do something it should | Check the tool isn't turned off (**Permissions → All tools**), the kill switch is off, and the agent's tool list includes it. |
| Every action is refused instantly | A "Never" rule may exist: `fox permissions list`, then `fox permissions revoke TOOL TARGET`. |
| Prompts time out | Raise `FOX_PERMISSION_TIMEOUT`. With `fox serve`, a client must be connected to answer. |
| A plugin doesn't appear | Its file name must not start with `_`. Check **Control** or `data/fox.log` for the load error. |
| Microphone not working | Check Windows privacy settings allow desktop apps to use the microphone, and that `voice` extras are installed. |
| The app window is blank | Install or repair the WebView2 Runtime. |
| Anything else | Open `data/fox.log` (**Settings → Open log**) and [open an issue](https://github.com/Agni-glitch/Assistent/issues/new/choose) with the relevant lines. Remove keys and personal data first. |
