# Security policy

Fox can act on a user's computer, so security reports are taken seriously and handled first.

## Supported versions

Fox is pre-1.0. Only the latest release and the `main` branch receive security fixes.

## Reporting a vulnerability

**Please don't report security problems in public issues, discussions or pull requests.**

Report privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability** ([direct link](https://github.com/Agni-glitch/fox/security/advisories/new)).

Please include:

- What an attacker can do, and what they need first (a malicious web page, an email, a plugin, local access...).
- Steps to reproduce, ideally with the smallest example.
- The Fox version or commit, Windows version, and the model used if relevant.

You can expect an acknowledgement within 7 days, and an update on the fix plan after the issue is confirmed. Once a fix is released, you'll be credited in the advisory unless you prefer otherwise.

## In scope

Anything that breaks the protections described in [docs/security-model.md](docs/security-model.md), for example:

- A tool action that runs without passing the permission gate, or a critical action that runs without asking.
- A remembered answer that applies to more than its target.
- The model or a tool reading `.env` or the `data` folder, or changing permissions, the kill switch, turned-off tools, agents, plugins or Fox's code.
- A prompt that misrepresents what an action will do.
- Reaching the local API without the token, from another device, or from an origin that isn't allowed.
- Script execution in the desktop app from a model reply or tool output.
- `fetch_webpage` reaching private or local network addresses.
- A build that bundles a user's `.env` or data.

## Out of scope

- Actions the user explicitly approved, including `run_powershell` commands.
- Malicious plugins the user installed themselves (plugins are trusted code by design).
- A model being talked into *asking* for something harmful through prompt injection, as long as the prompt shows accurately what will happen. Ideas to reduce this are still welcome as regular issues.
- Attacks that need control of the user's Windows account already.
- Vulnerabilities in model providers or third-party libraries (report those upstream; let us know if Fox needs to update).
