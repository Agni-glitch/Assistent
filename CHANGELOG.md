# Changelog

All notable changes to Fox are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Fox uses [Semantic Versioning](https://semver.org/). Before 1.0, minor versions may include breaking changes to the plugin API and the local API.

## [Unreleased]

### Fixed

- Building `Fox.exe` bundles only the example plugins, never plugins of your own, and the build and release scripts refuse a bundle that has any.

## [0.2.0] - 2026-11-03

First release with a downloadable `Fox.exe`.

### Added

- Attachments in chat: documents (PDF, Word, text, code, CSV), images and HAR files, with **+**, drag and drop, or paste. Long documents are read in parts with the new `read_attachment` tool.
- A **Models** page: add any model, including OpenAI-compatible servers such as OpenRouter, DeepSeek and LM Studio, test it, and use it by name in the fallback order or in an agent. Keys stay in `data/models.json`.
- A **Model** menu in chat to pick the model for a conversation, and `FOX_VISION_CHAIN` for messages with images.
- Browser capture: `/api/captures` accepts requests and responses from a browser extension and removes secrets before storing them. Five network tools summarise, list, show, import (HAR) and clear captured traffic. A reference Chrome extension is in `extensions/chrome-capture`.
- Replies render task checklists (`- [x]`, `- [ ]`, `- [~]`, `- [!]`) with a progress bar, nested lists, table alignment, status labels in tables, strikethrough, and code blocks with a Copy button. Fox ends multi-step work with a checklist.
- Consecutive tool steps in chat are grouped with a done/stopped count.
- New settings: `FOX_VISION_CHAIN`, `FOX_MAX_UPLOAD_MB`, `FOX_MAX_ATTACHMENT_CHARS`, `FOX_MAX_CAPTURES`.

### Changed

- The desktop app's API now uses `FOX_SERVER_PORT` (8765) when it's free, so a browser extension has a fixed address. It still falls back to any free port.
- Provider keys and the fallback order moved from **Settings** to the **Models** page.

## [0.1.0] - 2026-11-03

First public release.

### Added

- Chat in the terminal (`fox chat`) and a desktop app (`fox gui`, `Fox.exe`).
- Permission gate with safe, normal and critical risk levels, per-target remembered answers, timeouts that refuse, and an audit log.
- Kill switch and per-tool on/off switches, out of the model's reach.
- 47 built-in tools for apps, windows, files, web, email, system settings, clipboard, PowerShell, memory, reminders and agents.
- Model fallback across Azure OpenAI, OpenAI, Anthropic, Google Gemini, Groq and Ollama, with cooldowns.
- Specialist agents defined in YAML, with their own tools and model chains; three starter agents.
- Hot-reloaded Python plugins with an in-app editor.
- Voice: push-to-talk and wake word, spoken permission answers, offline and cloud speech engines.
- Local REST and WebSocket API with token authentication.
- Untrusted-content marking to reduce prompt injection.
- `build.bat` packaging with a bundle safety check and self-test.

[Unreleased]: https://github.com/Agni-glitch/Assistent/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Agni-glitch/Assistent/releases/tag/v0.2.0
[0.1.0]: https://github.com/Agni-glitch/Assistent/tree/b76ddd3
