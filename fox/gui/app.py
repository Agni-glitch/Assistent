"""The desktop app: a native window showing the page served by Fox's local API.

The API runs inside this process, on FOX_SERVER_PORT when it's free and on any free port otherwise.
The token reaches the page in the part of the address after #, which is never sent over the network.
"""

from __future__ import annotations

import ctypes
import html
import logging
import multiprocessing
import os
import shutil
import socket
import sys
import threading
import time
import traceback
from ctypes import wintypes
from pathlib import Path

from fox.config import ROOT
from fox.gui.launcher import RETRY_ENV

log = logging.getLogger("fox.gui")

TITLE = "Fox"
APP_ID = "Fox.Assistant"
STARTUP_TIMEOUT = 90
WINDOW_SHOWN_TIMEOUT = 60
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
MB_ICONERROR = 0x10
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9
FLASHW_ALL_UNTIL_FOCUSED = 0x3 | 0xC
WINDOW_CLASS_PREFIX = "WindowsForms10."  # the kind of window pywebview creates

PAGE_STYLE = (
    "html,body{height:100%;margin:0;background:#191613;color:#f3ece4;font-family:'Segoe UI',sans-serif}"
    "body{display:flex;align-items:center;justify-content:center;text-align:center}"
    "main{max-width:640px;padding:24px}h1{font-size:20px;font-weight:600;margin:0 0 8px}"
    "p{color:#b5aa9e;margin:0}pre{text-align:left;white-space:pre-wrap;color:#ffb48a;font-size:13px}"
)
SPLASH = f"<!doctype html><title>{TITLE}</title><style>{PAGE_STYLE}</style><main><h1>Fox</h1><p>Starting up...</p></main>"

# Everything Fox only imports when it's needed. In Fox.exe, a module the build missed would otherwise
# fail much later, in the middle of a conversation.
LAZY_MODULES = [
    "webview", "clr",
    "langchain_openai", "langchain_google_genai", "langchain_groq", "langchain_anthropic", "langchain_ollama",
    "tiktoken", "tiktoken_ext.openai_public",
    "numpy", "sounddevice", "miniaudio", "pyttsx3", "pyttsx3.drivers.sapi5", "edge_tts",
    "faster_whisper", "ctranslate2", "onnxruntime", "av",
    "comtypes", "pycaw.pycaw", "screen_brightness_control", "send2trash", "winotify",
    "pyautogui", "pygetwindow", "pyperclip", "PIL.ImageGrab",
    "ddgs", "bs4", "pypdf", "docx",
    "fox.voice.service",
]  # fmt: skip


def resource_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", ROOT))


def quiet_streams() -> None:
    """A windowed exe has no console, and libraries that write to one would crash."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def seed_home() -> None:
    """First run outside the project: create .env and the starter agents from the bundled copies."""
    defaults = resource_dir() / "defaults"
    if not getattr(sys, "frozen", False) or not defaults.is_dir():
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    if not (ROOT / ".env").exists():
        shutil.copyfile(defaults / ".env.example", ROOT / ".env")
    for folder in ("agents", "plugins"):
        if not (ROOT / folder).exists():
            shutil.copytree(defaults / folder, ROOT / folder)


def message_box(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, MB_ICONERROR)


class FlashInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("dwFlags", wintypes.DWORD),
        ("uCount", wintypes.UINT),
        ("dwTimeout", wintypes.DWORD),
    ]


def is_fox_window(title: str, class_name: str) -> bool:
    """The title alone isn't enough: File Explorer showing a folder called Fox has that title too."""
    return title == TITLE and class_name.startswith(WINDOW_CLASS_PREFIX)


def find_window(this_process_only: bool = False) -> int:
    user32 = ctypes.WinDLL("user32")
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    found = 0

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def check(hwnd, _):
        nonlocal found
        title, class_name = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 64)
        user32.GetClassNameW(hwnd, class_name, 256)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if is_fox_window(title.value, class_name.value) and (not this_process_only or pid.value == os.getpid()):
            found = hwnd
            return False
        return True

    user32.EnumWindows(check, 0)
    return found or 0


def flash_window() -> None:
    """A permission prompt is useless if the window is hidden behind others, so light up the taskbar."""
    hwnd = find_window(this_process_only=True)
    if hwnd:
        info = FlashInfo(ctypes.sizeof(FlashInfo), hwnd, FLASHW_ALL_UNTIL_FOCUSED, 0, 0)
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))


def claim_single_instance():
    """Returns a handle to keep, or None if Fox is already open. Two copies would both fire reminders."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, "Local\\FoxAssistant")
    return None if ctypes.get_last_error() == ERROR_ALREADY_EXISTS else handle


def focus_existing_window() -> None:
    hwnd = find_window()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
        ctypes.windll.user32.SetForegroundWindow(hwnd)


def open_local_socket(host: str, attempts: int = 20, preferred: int = 0) -> socket.socket:
    """Claim a free port, trying `preferred` first. Windows can offer a port and then refuse to listen on it,
    so try another.

    A fixed port gives a browser extension an address that doesn't change; when `fox serve` or another
    program holds it, any free port will do.
    """
    for attempt in range(attempts):
        sock = socket.socket()
        try:
            # Stops any other program taking over this port while Fox is using it.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((host, preferred if attempt == 0 else 0))
            sock.listen()
            return sock
        except OSError:
            sock.close()
            if attempt == attempts - 1:
                raise
    raise RuntimeError("No port was tried.")


class EmbeddedServer:
    def __init__(self) -> None:
        self.server = None
        self.thread: threading.Thread | None = None

    def start(self) -> str:
        """Start the API and return the address for the window, including the token."""
        import uvicorn

        from fox.config import get_settings
        from fox.server import HOST, create_app, load_token

        settings = get_settings()
        sock = open_local_socket(HOST, preferred=settings.fox_server_port)
        port = sock.getsockname()[1]
        token = load_token(settings)
        app = create_app(port=port, on_attention=flash_window)
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="warning", log_config=None))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [sock]}, daemon=True)
        self.thread.start()

        deadline = time.monotonic() + STARTUP_TIMEOUT
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("The local API didn't start. See data/fox.log.")
            time.sleep(0.05)
        return f"http://{HOST}:{port}/#token={token}"

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(5)


def run_window() -> None:
    import webview

    server = EmbeddedServer()
    window = webview.create_window(TITLE, html=SPLASH, width=1200, height=800, min_size=(760, 520), background_color="#191613")

    def boot() -> None:
        # Show the window first, so the "Starting up" page isn't held back by Fox's slow imports.
        window.events.shown.wait(WINDOW_SHOWN_TIMEOUT)
        try:
            window.load_url(server.start())
        except Exception as exc:
            log.exception("Fox could not start")
            details = html.escape(f"{type(exc).__name__}: {exc}")
            window.load_html(
                f"<!doctype html><title>{TITLE}</title><style>{PAGE_STYLE}</style><main><h1>Fox could not start</h1>"
                f"<p>Check your settings in {html.escape(str(ROOT / '.env'))}.</p><pre>{details}</pre></main>"
            )

    try:
        webview.start(boot, gui="edgechromium")
    finally:
        server.stop()


def self_check(report: Path) -> int:
    """Used by the build script: load everything the app can need, without opening a window."""
    import importlib

    problems = []
    for name in LAZY_MODULES:
        try:
            importlib.import_module(name)
        except BaseException as exc:
            problems.append(f"import {name}: {type(exc).__name__}: {exc}")

    try:
        from fox.config import get_settings
        from fox.runtime import load_tools
        from fox.server import STATIC_DIR
        from fox.voice.tts import _pyttsx3

        settings = get_settings()
        load_tools(settings)
        for name in ("index.html", "app.js", "markdown.js", "style.css", "fox.svg"):
            if not (STATIC_DIR / name).is_file():
                problems.append(f"missing page file: {name}")
        import faster_whisper.utils

        assets = Path(faster_whisper.utils.get_assets_path())
        if not list(assets.glob("*.onnx")):
            problems.append(f"missing speech detection model in {assets}")
        import tiktoken

        tiktoken.registry.list_encoding_names()
        samples, _ = _pyttsx3(settings, "Fox self check.")
        if not len(samples):
            problems.append("the offline voice produced no audio")
    except BaseException:
        problems.append(traceback.format_exc())

    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(problems) or "OK", encoding="utf-8")
    return 1 if problems else 0


def main() -> None:
    multiprocessing.freeze_support()
    quiet_streams()
    if "--self-check" in sys.argv:
        report = Path(sys.argv[sys.argv.index("--self-check") + 1])
        os._exit(self_check(report))

    instance = claim_single_instance()
    if instance is None:
        focus_existing_window()
        return
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)

    try:
        seed_home()
        from fox.config import get_settings
        from fox.runtime import setup_logging

        try:
            setup_logging(get_settings())
            if os.environ.get(RETRY_ENV, "0") != "0":
                log.warning("Restarted after a crash at startup (retry %s)", os.environ[RETRY_ENV])
        except ValueError:
            pass  # a bad value in .env. The window shows it, and the user can fix it.
        run_window()
    except ImportError as exc:
        message_box(f"The desktop app needs a library that isn't installed:\n{exc}\n\nRun: uv sync --all-extras")
    except Exception as exc:
        log.exception("Fox stopped")
        message_box(
            f"Fox could not open its window.\n\n{exc}\n\n"
            f"If this mentions WebView2, install the Evergreen Runtime from:\n{WEBVIEW2_URL}"
        )
    finally:
        logging.shutdown()
        # Audio and tool threads may still be blocked, and they must not keep Fox alive after the window closes.
        os._exit(0)


if __name__ == "__main__":
    main()
