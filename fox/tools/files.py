"""Read, write, move and delete files, and keep Fox's own settings out of reach."""

from __future__ import annotations

import fnmatch
import os
import shutil
import sys
from pathlib import Path

from fox.config import ROOT, get_settings
from fox.core.permissions import Risk
from fox.core.tooling import ToolError, fox_tool

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "$Recycle.Bin", "AppData", "Windows"}
EXECUTABLE_SUFFIXES = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".wsc", ".lnk", ".url", ".reg", ".hta", ".cpl", ".pif", ".py", ".pyw", ".jar",
    ".msc", ".appref-ms", ".library-ms", ".settingcontent-ms",
}  # fmt: skip


def is_executable(path: Path) -> bool:
    # Windows ignores trailing dots and spaces, so "evil.exe." still runs.
    return Path(path.name.rstrip(". ")).suffix.lower() in EXECUTABLE_SUFFIXES


def resolve(path: str) -> Path:
    return Path(os.path.expandvars(path.strip().strip('"'))).expanduser().resolve()


def _inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _secret_paths() -> list[Path]:
    return [(ROOT / ".env").resolve(), get_settings().fox_data_dir.resolve()]


def _own_code_paths() -> list[Path]:
    """Fox's code, the Python it runs on, and what builds or becomes Fox.exe."""
    names = ("fox", "Fox.exe", "Fox.lnk", "_internal", "scripts", ".venv", "fox.spec", "build.bat", "pyproject.toml")
    paths = [ROOT / name for name in names]
    if getattr(sys, "frozen", False):
        # Not the whole folder: a copy of Fox.exe on the Desktop mustn't lock the Desktop.
        exe = Path(sys.executable)
        paths += [exe, exe.parent / "_internal"]
    return [path.resolve() for path in paths]


def _config_paths() -> list[Path]:
    s = get_settings()
    return [*_secret_paths(), s.fox_agents_dir.resolve(), s.fox_plugins_dir.resolve(), *_own_code_paths()]


def check_readable(path: Path) -> Path:
    """API keys, the API token and permission rules stay out of the model's reach."""
    if any(_inside(path, p) for p in _secret_paths()):
        raise ToolError(f"{path} holds Fox's private settings and can't be read by tools.")
    return path


def check_writable(path: Path) -> Path:
    """Fox can't edit its own permissions, keys or code, so it can't grant itself anything."""
    if any(_inside(path, p) or _inside(p, path) for p in _config_paths()):
        raise ToolError(f"{path} contains Fox's own settings or code and can't be changed by tools.")
    return path


def _check_deletable(path: Path) -> Path:
    check_writable(path)
    home = Path.home().resolve()
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()
    programs = [Path(os.environ[v]).resolve() for v in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(v)]
    if path == Path(path.anchor) or _inside(home, path) or any(_inside(path, p) for p in [system, *programs]):
        raise ToolError(f"{path} is a protected system or home location and can't be deleted.")
    return path


def _folder_scope(path: str) -> str:
    target = resolve(path)
    return str(target if target.is_dir() else target.parent)


@fox_tool(scope=lambda a: _folder_scope(a["path"]), describe=lambda a: f"List folder: {resolve(a['path'])}", untrusted=True)
def list_folder(path: str) -> str:
    """List the files and folders in a directory. Accepts ~ and %VARS%."""
    target = check_readable(resolve(path))
    if not target.is_dir():
        raise ToolError(f"{target} is not a folder.")
    rows = []
    for item in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))[:500]:
        try:
            rows.append(f"{item.name}  ({item.stat().st_size} bytes)" if item.is_file() else f"{item.name}\\")
        except OSError:
            rows.append(item.name)
    return "\n".join(rows) or "The folder is empty."


@fox_tool(
    scope=lambda a: str(resolve(a["folder"])),
    describe=lambda a: f"Search for '{a['pattern']}' in: {resolve(a['folder'])}",
    untrusted=True,
    timeout=60,
)
def search_files(folder: str, pattern: str, max_results: int = 50) -> str:
    """Find files by name under a folder, using a pattern such as '*.pdf' or '*invoice*'."""
    root = check_readable(resolve(folder))
    if not root.is_dir():
        raise ToolError(f"{root} is not a folder.")
    found: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        found += [os.path.join(current, f) for f in fnmatch.filter(files, pattern)]
        if len(found) >= max_results:
            break
    return "\n".join(found[:max_results]) or "No files found."


def _read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    if suffix == ".docx":
        from docx import Document

        return "\n".join(p.text for p in Document(path).paragraphs)
    with open(path, "rb") as handle:
        if b"\0" in handle.read(4096):
            raise ToolError(f"{path.name} is a binary file, so it can't be read as text.")
    return path.read_text(encoding="utf-8", errors="replace")


@fox_tool(scope=lambda a: _folder_scope(a["path"]), describe=lambda a: f"Read file: {resolve(a['path'])}", untrusted=True)
def read_file(path: str) -> str:
    """Read a text, PDF or Word (.docx) file."""
    target = check_readable(resolve(path))
    if not target.is_file():
        raise ToolError(f"File not found: {target}")
    return _read_text(target)


def _describe_write(args: dict) -> str:
    target = check_writable(resolve(args["path"]))
    verb = "Overwrite existing file" if target.exists() else "Create file"
    if target.exists() and not args["overwrite"]:
        raise ToolError(f"{target} already exists. Ask the user, then call again with overwrite=True.")
    return f"{verb}: {target}\n\n{args['content']}"


@fox_tool(
    risk=lambda a: Risk.CRITICAL if a["overwrite"] else Risk.NORMAL,
    scope=lambda a: str(resolve(a["path"])),
    describe=_describe_write,
)
def write_file(path: str, content: str, overwrite: bool = False) -> str:
    """Create a text file. Setting overwrite=True replaces an existing file."""
    target = check_writable(resolve(path))
    if target.exists() and not overwrite:
        raise ToolError(f"{target} already exists.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {target}."


def _describe_move(args: dict) -> str:
    source, dest = check_writable(resolve(args["source"])), check_writable(resolve(args["destination"]))
    if not source.exists():
        raise ToolError(f"{source} does not exist.")
    if dest.is_file():
        raise ToolError(f"{dest} already exists, and moving won't overwrite it.")
    return f"Move: {source}\nTo: {dest}"


@fox_tool(scope=lambda a: str(resolve(a["source"])), describe=_describe_move)
def move_path(source: str, destination: str) -> str:
    """Move or rename a file or folder. Never overwrites an existing file."""
    source_path, dest = check_writable(resolve(source)), check_writable(resolve(destination))
    if dest.is_file():
        raise ToolError(f"{dest} already exists.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    return f"Moved to {shutil.move(str(source_path), str(dest))}."


def _describe_delete(args: dict) -> str:
    target = _check_deletable(resolve(args["path"]))
    if not target.exists():
        raise ToolError(f"{target} does not exist.")
    kind = "folder and everything in it" if target.is_dir() else "file"
    return f"Move to Recycle Bin ({kind}): {target}"


@fox_tool(risk=Risk.CRITICAL, scope=lambda a: str(resolve(a["path"])), describe=_describe_delete)
def delete_path(path: str) -> str:
    """Delete a file or folder by moving it to the Recycle Bin."""
    from send2trash import send2trash

    target = _check_deletable(resolve(path))
    send2trash(str(target))
    return f"Moved {target} to the Recycle Bin."


def _describe_open_path(args: dict) -> str:
    target = check_readable(resolve(args["path"]))
    if not target.exists():
        raise ToolError(f"{target} does not exist.")
    if is_executable(target):
        return f"RUN this program or script: {target}\nIt can do anything your account can do."
    return f"Open with its default app: {target}"


@fox_tool(
    risk=lambda a: Risk.CRITICAL if is_executable(resolve(a["path"])) else Risk.NORMAL,
    scope=lambda a: str(resolve(a["path"])),
    describe=_describe_open_path,
)
def open_path(path: str) -> str:
    """Open a file or folder with its default Windows app."""
    target = check_readable(resolve(path))
    os.startfile(target)
    return f"Opened {target}."
