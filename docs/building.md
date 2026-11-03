# Building Fox.exe

`build.bat` packages Fox into a Windows desktop app with PyInstaller.

```powershell
.\build.bat
```

It installs dependencies, runs the tests, builds `Fox.exe` and its `_internal` folder in the project root, runs the app's self-check, and creates the shortcut `Fox.lnk`. The build takes a few minutes and about 400 MB of disk. Close Fox before rebuilding.

## Options

| Command | What it does |
|---|---|
| `.\build.bat` | Full build, with `Fox.lnk` in the project folder. |
| `.\build.bat -SkipTests` | Skips the tests. |
| `.\build.bat -NoShortcut` | Builds without creating a shortcut. |
| `.\build.bat -Desktop` | Also puts a shortcut on the Desktop. |
| `.\build.bat -StartMenu` | Also adds Fox to the Start menu. |
| `.\scripts\create_shortcut.ps1` | Only recreates the shortcut. Takes `-Desktop` and `-StartMenu` too. |

## Safety checks

The build stops with an error if:

- the bundle contains your `.env`, chats or permissions, so a shared build never leaks your keys or data;
- the bundle contains a plugin of your own. Only `plugins/_example*.py` are bundled, as starters for a new install; your own plugins stay on your computer, the same way `.gitignore` keeps them out of git;
- `Fox.exe` fails its self-check, which loads every bundled library and tests the offline voice.

## Files involved

| File | Purpose |
|---|---|
| `build.bat`, `scripts/build.ps1` | The build steps. |
| `fox.spec` | What goes into the bundle. If a new dependency imports modules by name at run time, add it here. |
| `scripts/fox_app.py` | The app's entry point. |
| `scripts/make_icon.py` | Draws `assets/fox.ico`. |
| `scripts/create_shortcut.ps1` | Creates `Fox.lnk`. |

## Where the app keeps its data

| Where `Fox.exe` is | Where it keeps `.env`, `data`, `agents` and `plugins` |
|---|---|
| The project's root folder | The project's own folders, shared with the `fox` command. Rebuilding doesn't touch them. |
| Copied anywhere else | `%LOCALAPPDATA%\Fox`, created on first run with the starter agents and no keys. |

## Moving and sharing the app

- `Fox.exe` needs the `_internal` folder beside it, so move or copy them together.
- If you move the project, run `.\scripts\create_shortcut.ps1` again; a shortcut stores the full path.
- `Fox.exe` isn't code-signed, so Windows SmartScreen may warn about it on other computers.
- The app needs the Microsoft Edge WebView2 Runtime, which comes with Windows 11. If it's missing, Fox shows a message with the download link.
- Plugins in `Fox.exe` can only import libraries that are bundled. See [Writing plugins](plugins.md#plugins-in-foxexe).

## Releasing on GitHub

`Fox.exe`, `_internal` and `Fox.lnk` are in `.gitignore`; don't commit them. Releases carry the app as a zip instead.

1. Set the version in `pyproject.toml` and `fox/__init__.py`, move the changelog's *Unreleased* entries under the new version, and merge to `main`.
2. Run `.\build.bat` so `Fox.exe` reports the new version.
3. Run `.\scripts\package_release.ps1`. It zips `Fox.exe` and `_internal` into `dist\Fox-v<version>-windows-x64.zip`, refuses if any private file (`.env`, `fox.db`, `api_token.txt`, `permissions.json`, `models.json`) is inside, and prints the SHA-256.
4. Tag and publish, with the changelog section as the notes:

```powershell
git tag v0.2.0
git push origin v0.2.0
gh release create v0.2.0 dist\Fox-v0.2.0-windows-x64.zip --title "Fox 0.2.0" --notes-file notes.md
```

The zip holds no settings or data. A downloaded `Fox.exe` keeps its data in `%LOCALAPPDATA%\Fox`, starting from the bundled `.env.example`, starter agents and example plugins.
