"""Windows control: volume, media, windows, clipboard, power, keyboard and PowerShell."""

from __future__ import annotations

import ctypes
import re
import subprocess
import time
from datetime import datetime
from typing import Literal

import psutil

from fox.config import get_settings
from fox.core.permissions import Risk
from fox.core.tooling import ToolError, fox_tool

NO_WINDOW = subprocess.CREATE_NO_WINDOW


def notify(title: str, message: str) -> None:
    from winotify import Notification

    Notification(app_id="Fox", title=title, msg=message).show()


@fox_tool(risk=Risk.SAFE)
def get_datetime() -> str:
    """Get the current local date and time."""
    return datetime.now().strftime("%A, %d %B %Y, %H:%M:%S")


@fox_tool(risk=Risk.SAFE)
def get_system_info() -> str:
    """Get CPU, memory, disk, battery and uptime information."""
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")
    battery = psutil.sensors_battery()
    uptime = int((time.time() - psutil.boot_time()) // 60)
    info = [
        f"CPU: {psutil.cpu_percent(interval=0.3)}%",
        f"Memory: {memory.percent}% of {memory.total // 2**30} GB used",
        f"Disk C: {disk.percent}% of {disk.total // 2**30} GB used",
        f"Uptime: {uptime // 60}h {uptime % 60}m",
    ]
    if battery:
        info.append(f"Battery: {battery.percent}%, {'charging' if battery.power_plugged else 'on battery'}")
    return "\n".join(info)


def _volume_control():
    import comtypes
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()  # tools run on worker threads
    return AudioUtilities.GetSpeakers().EndpointVolume


@fox_tool(risk=Risk.SAFE)
def get_volume() -> str:
    """Get the current speaker volume and mute state."""
    control = _volume_control()
    return f"Volume: {round(control.GetMasterVolumeLevelScalar() * 100)}%, muted: {bool(control.GetMute())}"


@fox_tool(describe=lambda a: f"Set volume to {a['level']}%")
def set_volume(level: int) -> str:
    """Set the speaker volume from 0 to 100."""
    if not 0 <= level <= 100:
        raise ToolError("Volume must be between 0 and 100.")
    _volume_control().SetMasterVolumeLevelScalar(level / 100, None)
    return f"Volume set to {level}%."


@fox_tool(describe=lambda a: "Mute the speakers" if a["mute"] else "Unmute the speakers")
def set_mute(mute: bool) -> str:
    """Mute or unmute the speakers."""
    _volume_control().SetMute(int(mute), None)
    return "Muted." if mute else "Unmuted."


@fox_tool(describe=lambda a: f"Set screen brightness to {a['level']}%")
def set_brightness(level: int) -> str:
    """Set the screen brightness from 0 to 100."""
    import screen_brightness_control as sbc

    if not 0 <= level <= 100:
        raise ToolError("Brightness must be between 0 and 100.")
    sbc.set_brightness(level)
    return f"Brightness set to {level}%."


MEDIA_KEYS = {"play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack", "stop": "stop"}


@fox_tool(scope=lambda a: a["action"], describe=lambda a: f"Media control: {a['action']}")
def media_control(action: Literal["play_pause", "next", "previous", "stop"]) -> str:
    """Control whatever media is playing, such as Spotify or YouTube."""
    import pyautogui

    pyautogui.press(MEDIA_KEYS[action])
    return f"Sent {action}."


@fox_tool(describe=lambda a: "Lock the computer")
def lock_screen() -> str:
    """Lock the Windows session."""
    if not ctypes.windll.user32.LockWorkStation():
        raise ToolError("Windows refused to lock the session.")
    return "Locked."


POWER_TEXT = {
    "shutdown": "Shut down this computer in 60 seconds",
    "restart": "Restart this computer in 60 seconds",
    "sleep": "Put this computer to sleep now",
    "cancel": "Cancel a scheduled shutdown or restart",
}


@fox_tool(
    risk=lambda a: Risk.NORMAL if a["action"] == "cancel" else Risk.CRITICAL,
    scope=lambda a: a["action"],
    describe=lambda a: POWER_TEXT[a["action"]],
)
def power_action(action: Literal["shutdown", "restart", "sleep", "cancel"]) -> str:
    """Shut down, restart or sleep the computer, or cancel a pending shutdown. Shutdown and restart wait 60 seconds."""
    commands = {
        "shutdown": ["shutdown", "/s", "/t", "60"],
        "restart": ["shutdown", "/r", "/t", "60"],
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "cancel": ["shutdown", "/a"],
    }
    done = subprocess.run(commands[action], capture_output=True, text=True, creationflags=NO_WINDOW)
    if done.returncode != 0:
        raise ToolError((done.stderr or done.stdout).strip() or f"{action} failed.")
    return f"{POWER_TEXT[action]}: done." + (" Use action='cancel' to stop it." if action in ("shutdown", "restart") else "")


@fox_tool(describe=lambda a: "See the titles of all your open windows")
def list_windows() -> str:
    """List the titles of open windows."""
    import pygetwindow

    return "\n".join(t for t in pygetwindow.getAllTitles() if t.strip()) or "No open windows."


def _find_window(title: str):
    import pygetwindow

    windows = [w for w in pygetwindow.getWindowsWithTitle(title) if w.title.strip()]
    if not windows:
        raise ToolError(f"No open window has '{title}' in its title.")
    return windows[0]


@fox_tool(
    scope=lambda a: a["title"],
    describe=lambda a: f"{a['action'].capitalize()} the window titled: {_find_window(a['title']).title}",
)
def window_action(title: str, action: Literal["focus", "minimize", "maximize", "restore", "close"]) -> str:
    """Focus, minimize, maximize, restore or close a window, found by part of its title."""
    window = _find_window(title)
    {
        "focus": window.activate,
        "minimize": window.minimize,
        "maximize": window.maximize,
        "restore": window.restore,
        "close": window.close,
    }[action]()
    return f"Did {action} on '{window.title}'."


@fox_tool(describe=lambda a: "Take a screenshot of your screen")
def take_screenshot() -> str:
    """Save a screenshot of the screen as an image file in Fox's data folder. Returns the path."""
    from PIL import ImageGrab

    folder = get_settings().fox_data_dir / "screenshots"
    folder.mkdir(exist_ok=True)
    path = folder / f"screenshot-{datetime.now():%Y%m%d-%H%M%S}.png"
    ImageGrab.grab(all_screens=True).save(path)
    return f"Screenshot saved to {path}."


@fox_tool(describe=lambda a: "Read what's on your clipboard", untrusted=True)
def get_clipboard() -> str:
    """Read the text on the clipboard."""
    import pyperclip

    return pyperclip.paste() or "The clipboard is empty."


@fox_tool(describe=lambda a: f"Replace your clipboard with:\n{a['text']}")
def set_clipboard(text: str) -> str:
    """Copy text to the clipboard."""
    import pyperclip

    pyperclip.copy(text)
    return "Copied to the clipboard."


@fox_tool(describe=lambda a: f"Show a notification: {a['title']}\n{a['message']}")
def show_notification(title: str, message: str) -> str:
    """Show a Windows notification."""
    notify(title, message)
    return "Notification shown."


@fox_tool(scope=lambda a: a["page"], describe=lambda a: f"Open Windows Settings page: {a['page']}")
def open_settings(page: str = "") -> str:
    """Open Windows Settings, such as page 'bluetooth', 'network-wifi', 'display' or 'windowsupdate'."""
    import os

    if not re.fullmatch(r"[a-zA-Z0-9-]*", page):
        raise ToolError("That isn't a valid settings page name.")
    os.startfile(f"ms-settings:{page}")
    return f"Opened Settings {page}."


@fox_tool(risk=Risk.CRITICAL, describe=lambda a: f"Type into the active window:\n{a['text']}")
def type_text(text: str) -> str:
    """Type text into whichever window is active, as if on the keyboard. Focus the right window first."""
    import pyautogui

    pyautogui.write(text, interval=0.01)
    return "Typed the text."


@fox_tool(risk=Risk.CRITICAL, describe=lambda a: f"Press keys in the active window: {' + '.join(a['keys'])}")
def press_keys(keys: list[str]) -> str:
    """Press a key or a shortcut in the active window, such as ['ctrl', 's'] or ['enter']."""
    import pyautogui

    unknown = [k for k in keys if k.lower() not in pyautogui.KEYBOARD_KEYS]
    if unknown:
        raise ToolError(f"Unknown keys: {unknown}")
    pyautogui.hotkey(*[k.lower() for k in keys])
    return f"Pressed {' + '.join(keys)}."


@fox_tool(
    risk=Risk.CRITICAL,
    describe=lambda a: f"Run this PowerShell command:\n{a['command']}\nIt can do anything your account can do.",
    timeout=None,
)
def run_powershell(command: str, timeout_seconds: int = 60) -> str:
    """Run a PowerShell command when no other tool can do the job. Returns its output."""
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=min(timeout_seconds, 600), creationflags=NO_WINDOW,
        )  # fmt: skip
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"The command timed out after {exc.timeout} seconds.") from exc
    output = (done.stdout + done.stderr).strip() or "(no output)"
    return f"Exit code {done.returncode}\n{output}"
