"""Open, close and find applications."""

from __future__ import annotations

import ctypes
import difflib
import json
import os
import shutil
import subprocess
import time

import psutil

from fox.core.permissions import Risk
from fox.core.tooling import ToolError, fox_tool

NO_WINDOW = subprocess.CREATE_NO_WINDOW
PROTECTED_PROCESSES = {
    "csrss.exe", "dwm.exe", "explorer.exe", "lsass.exe", "services.exe", "smss.exe",
    "svchost.exe", "system", "wininit.exe", "winlogon.exe",
}  # fmt: skip

_app_cache: tuple[float, dict[str, str]] | None = None


def installed_apps() -> dict[str, str]:
    """Start menu apps (desktop and Store) as {name: AppID}. Cached for five minutes."""
    global _app_cache
    if _app_cache and time.monotonic() - _app_cache[0] < 300:
        return _app_cache[1]
    out = subprocess.run(
        [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress",
        ],
        capture_output=True, text=True, encoding="utf-8", timeout=30, creationflags=NO_WINDOW,
    ).stdout  # fmt: skip
    entries = json.loads(out) if out.strip() else []
    if isinstance(entries, dict):
        entries = [entries]
    apps = {e["Name"]: e["AppID"] for e in entries if e.get("Name") and e.get("AppID")}
    _app_cache = (time.monotonic(), apps)
    return apps


def find_apps(name: str, apps: dict[str, str]) -> list[str]:
    """Best matches for a spoken or typed app name. More than one result means it's ambiguous."""
    wanted = name.strip().lower()
    by_lower = {app.lower(): app for app in apps}
    if wanted in by_lower:
        return [by_lower[wanted]]
    starts = [app for low, app in by_lower.items() if low.startswith(wanted)]
    if starts:
        return sorted(starts)
    contains = [app for low, app in by_lower.items() if wanted in low]
    if contains:
        return sorted(contains)
    return [by_lower[m] for m in difflib.get_close_matches(wanted, list(by_lower), n=3, cutoff=0.75)]


def _describe_open(args: dict) -> str:
    matches = find_apps(args["name"], installed_apps())
    if len(matches) == 1:
        return f"Open app: {matches[0]}"
    if matches:
        raise ToolError(f"'{args['name']}' matches several apps: {', '.join(matches[:10])}. Ask which one.")
    path = shutil.which(args["name"])
    if path:
        return f"Open program: {path}"
    raise ToolError(f"No installed app matches '{args['name']}'.")


@fox_tool(scope=lambda a: a["name"].strip(), describe=_describe_open)
def open_app(name: str) -> str:
    """Open an installed application by name, such as 'Notepad', 'Chrome' or 'Spotify'."""
    apps = installed_apps()
    matches = find_apps(name, apps)
    if len(matches) == 1:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{apps[matches[0]]}"])
        return f"Opened {matches[0]}."
    path = shutil.which(name)
    if path:
        os.startfile(path)
        return f"Opened {path}."
    raise ToolError(f"No installed app matches '{name}'.")


def _matching_processes(name: str) -> list[psutil.Process]:
    """Exact name first, then partial, since 'calculator' really runs as CalculatorApp.exe."""
    wanted = name.strip().lower().removesuffix(".exe")
    exact, partial = [], []
    for proc in psutil.process_iter(["name"]):
        proc_name = (proc.info["name"] or "").lower()
        if proc_name in PROTECTED_PROCESSES:
            continue
        if proc_name.removesuffix(".exe") == wanted:
            exact.append(proc)
        elif len(wanted) >= 3 and wanted in proc_name:
            partial.append(proc)
    return exact or partial


def _process_names(procs: list[psutil.Process]) -> str:
    return ", ".join(sorted({p.info["name"] for p in procs}))


def _store_app_windows(name: str) -> list:
    """Store app windows belong to ApplicationFrameHost.exe, so closing the app's process does nothing.

    Limited to that host, so 'calculator' can't match a browser tab with that word in its title.
    """
    import pygetwindow

    wanted = name.strip().lower().removesuffix(".exe").removesuffix("app")
    found = []
    for window in pygetwindow.getAllWindows() if wanted else []:
        if wanted not in window.title.lower():
            continue
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(window._hWnd, ctypes.byref(pid))
        try:
            if psutil.Process(pid.value).name().lower() == "applicationframehost.exe":
                found.append(window)
        except psutil.Error:
            pass
    return found


def _describe_close(args: dict) -> str:
    wanted = args["name"].strip().lower()
    if wanted in PROTECTED_PROCESSES or f"{wanted}.exe" in PROTECTED_PROCESSES:
        raise ToolError(f"{args['name']} is a core Windows process and can't be closed.")
    procs = _matching_processes(args["name"])
    if not procs:
        raise ToolError(f"No running process is named '{args['name']}'. Use list_running_apps to find its name.")
    names = _process_names(procs)
    if args["force"]:
        return f"FORCE close {names}\nUnsaved work in this app will be lost."
    return f"Close {names}"


@fox_tool(
    risk=lambda a: Risk.CRITICAL if a["force"] else Risk.NORMAL,
    scope=lambda a: a["name"].strip().removesuffix(".exe"),
    describe=_describe_close,
)
def close_app(name: str, force: bool = False) -> str:
    """Close a running application by process name, such as 'notepad' or 'chrome'.

    The app closes normally and may ask to save. Only set force=True if the user wants it killed.
    """
    procs = _matching_processes(name)
    if not procs:
        raise ToolError(f"No running process is named '{name}'.")
    names = _process_names(procs)
    is_store_app = bool(_store_app_windows(name))
    for proc in procs:
        cmd = ["taskkill", "/PID", str(proc.pid), "/T"] + (["/F"] if force else [])
        subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    for window in _store_app_windows(name):
        window.close()

    # taskkill only asks. The app decides, so check what really happened before reporting it.
    _, alive = psutil.wait_procs(procs, timeout=3)
    if is_store_app and not _store_app_windows(name):
        return f"Closed the {names} window."  # Store apps can stay suspended in the background
    if not alive:
        return f"Closed {names}."
    if force:
        raise ToolError(f"{names} is still running. Windows refused to end it.")
    raise ToolError(
        f"{names} is still running. It may be showing a save prompt or have no window. "
        "Tell the user, and only use force=True if they want it killed."
    )


@fox_tool(risk=Risk.SAFE)
def list_running_apps() -> str:
    """List running applications by process name, with memory use, largest first."""
    usage: dict[str, int] = {}
    for proc in psutil.process_iter(["name", "memory_info"]):
        name, mem = proc.info["name"], proc.info["memory_info"]
        if name and mem and name.lower() not in PROTECTED_PROCESSES:
            usage[name] = usage.get(name, 0) + mem.rss
    top = sorted(usage.items(), key=lambda kv: kv[1], reverse=True)[:40]
    return "\n".join(f"{name}: {mem // (1024 * 1024)} MB" for name, mem in top)


@fox_tool(risk=Risk.SAFE)
def list_installed_apps(filter: str = "") -> str:
    """List installed apps from the Start menu, optionally only names containing `filter`."""
    names = sorted(installed_apps(), key=str.lower)
    if filter:
        names = [n for n in names if filter.lower() in n.lower()]
    return "\n".join(names[:200]) or "No matching apps."
