# Changelog

All notable changes to Fox are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Fox uses [Semantic Versioning](https://semver.org/). Before 1.0, minor versions may include breaking changes to the plugin API and the local API.

## [Unreleased]

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

[Unreleased]: https://github.com/Agni-glitch/fox/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Agni-glitch/fox/releases/tag/v0.1.0
