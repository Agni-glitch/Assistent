# Contributing to Fox

Thanks for your interest in Fox! Bug reports, plugins, agents, documentation and code are all welcome. This guide explains how to get set up and what we look for in a contribution.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to contribute

- **Report a bug** with the [bug report form](https://github.com/Agni-glitch/Assistent/issues/new?template=bug_report.yml).
- **Suggest a feature** with the [feature request form](https://github.com/Agni-glitch/Assistent/issues/new?template=feature_request.yml).
- **Share a plugin or agent** (see below).
- **Improve the docs**: typos, unclear steps and missing examples are all worth a pull request.
- **Fix an issue.** Issues labelled `good first issue` are a good place to start. Comment on an issue before starting larger work so nobody duplicates it.

**Security problems:** please don't open a public issue. Follow [SECURITY.md](SECURITY.md).

## Development setup

You need Windows 11, Git and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```powershell
git clone https://github.com/Agni-glitch/Assistent.git
cd Assistent
uv sync --all-extras          # installs Fox, all extras and the dev tools
uv run pytest                 # run the tests (no API keys needed)
uv run ruff check .           # lint
uv run fox gui                # run the app from source
```

The tests use fake models and a temporary data folder, so they never touch your `.env`, chats or permissions, and never spend API credits.

## Making a change

1. Fork the repository and create a branch from `main`: `git checkout -b fix/short-description`.
2. Make your change, with tests where it makes sense.
3. Run `uv run pytest` and `uv run ruff check .`. Both must pass.
4. Update the docs if behaviour, settings or commands change, and add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md).
5. Commit with a clear message in the imperative mood ("Add volume step setting", not "added stuff").
6. Open a pull request and fill in the template.

Keep pull requests focused: one fix or feature per PR is much easier to review.

## Code style

- Follow the style of the surrounding code. Ruff enforces the basics (`uv run ruff check . --fix`).
- Type hints on public functions; `from __future__ import annotations` at the top of modules.
- Docstrings and comments explain **why**, briefly. A tool's docstring is read by the model, so write it for the model.
- User-facing text is plain, short and specific: say what happened and what to do next.
- No new dependency without a good reason. If one is needed, add it with `uv add`, commit `uv.lock`, and check whether `fox.spec` needs it listed for `Fox.exe`.

## Security-sensitive changes

Fox's core promise is that **nothing acts without passing the permission gate**. Pull requests that touch these areas get extra review:

- [`fox/core/permissions.py`](fox/core/permissions.py), [`fox/core/tooling.py`](fox/core/tooling.py), [`fox/core/control.py`](fox/core/control.py)
- File guards in [`fox/tools/files.py`](fox/tools/files.py)
- Authentication and origins in [`fox/server.py`](fox/server.py)
- How untrusted content reaches the model

Rules for any change:

- Every new action must be a tool registered with `@fox_tool`, with an honest risk level, a narrow scope and a truthful description. Nothing may run actions outside `ToolExecutor.execute`.
- The model must never be able to change its own permissions, the kill switch, turned-off tools, agents, plugins, keys or Fox's code.
- Failures while asking must refuse (fail closed).
- Add tests that prove the protection holds, not only that the feature works.

Read [docs/security-model.md](docs/security-model.md) before working on these areas.

## Adding a built-in tool

1. Put it in the right module under [`fox/tools/`](fox/tools/), or create a new module and add it to `TOOL_MODULES` in [`fox/runtime.py`](fox/runtime.py).
2. Follow [docs/plugins.md](docs/plugins.md): the same decorator and rules apply.
3. Add tests in [`tests/test_fox.py`](tests/test_fox.py) for its risk levels, scope and refusals.
4. Add it to the tool table in [docs/user-guide.md](docs/user-guide.md#built-in-tools).

## Contributing a plugin

Plugins that would help many people are welcome as examples:

1. Add the file as `plugins/_example_<name>.py`. The leading underscore keeps it from loading by default.
2. Start the file with a docstring that says what it does, what it needs (keys, accounts, libraries) and how to enable it.
3. Meet the [plugin checklist](docs/plugins.md#checklist).
4. Don't add new dependencies for an example plugin; use what Fox already installs (for example `httpx`), or explain in the docstring what to install.
5. If it's testable without a network or account, add a test in the style of [`tests/test_plugins.py`](tests/test_plugins.py).

Plugins that need a paid service or are very personal are better shared in your own repository; open an issue or discussion to link them.

## Contributing an agent

Add a YAML file under `agents/` only if it's broadly useful as a starter. Keep its tool list minimal and its description specific. See [docs/agents.md](docs/agents.md).

## Reporting bugs well

Include:

- Fox version (`/api/status` or the **Settings** page), Windows version and how you run Fox (source or `Fox.exe`).
- The model provider and model.
- What you asked, what you expected and what happened.
- Relevant lines from `data/fox.log`.

**Remove API keys, email addresses, file paths and other personal data** from logs and screenshots before posting.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE) that covers the project.
