"""What Fox.exe runs first: opens the app in a child process, and retries if Windows kills it at startup.

The packaged app sometimes dies inside Windows while its window is being created (0xC000041D, seen in about
one launch in ten, and never when running from source). Nothing has happened by then, so starting again is safe.
Only this code and this early count: a crash during real use is never retried, because actions may be under way.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

WINDOW_FLAG = "--window"
RETRY_ENV = "FOX_STARTUP_RETRY"
STARTUP_CRASH = 0xC000041D
STARTUP_SECONDS = 30
MAX_ATTEMPTS = 5
MB_ICONERROR = 0x10


def supervise() -> int:
    for attempt in range(MAX_ATTEMPTS):
        started = time.monotonic()
        env = {**os.environ, RETRY_ENV: str(attempt)}
        code = subprocess.call([sys.executable, WINDOW_FLAG, *sys.argv[1:]], env=env)
        if code != STARTUP_CRASH or time.monotonic() - started > STARTUP_SECONDS:
            return code
    ctypes.windll.user32.MessageBoxW(None, f"Fox crashed {MAX_ATTEMPTS} times while opening its window.", "Fox", MB_ICONERROR)
    return STARTUP_CRASH


def main() -> None:
    if getattr(sys, "frozen", False) and WINDOW_FLAG not in sys.argv and "--self-check" not in sys.argv:
        sys.exit(supervise())

    from fox.gui.app import main as run_app

    run_app()
