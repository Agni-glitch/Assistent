"""Live controls the user holds over Fox: the kill switch and which tools are turned off.

This is deliberately separate from the permission rules. Permissions decide whether a single
action is allowed; these decide whether Fox may act at all, and which tools even exist for it.

Only the user reaches this, through the GUI or CLI. No tool can, because the state lives in the
data folder, which file tools are forbidden to touch, and nothing here is exposed as a tool. So the
model can never turn its own kill switch off or switch a disabled tool back on.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("fox.control")


class Control:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.kill_switch = False
        self.disabled: set[str] = set()
        self._mtime = -1.0
        self._load()

    def _mtime_now(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return -1.0

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.kill_switch = bool(data.get("kill_switch", False))
            self.disabled = {str(name) for name in data.get("disabled", [])}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.error("Ignoring unreadable %s: %s", self.path, exc)
        self._mtime = self._mtime_now()

    def _refresh(self) -> None:
        """Pick up a change made by another process, such as `fox kill on` from a terminal."""
        with self._lock:
            if self._mtime_now() != self._mtime:
                self._load()

    def _save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"kill_switch": self.kill_switch, "disabled": sorted(self.disabled)}, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
            self._mtime = self._mtime_now()

    def set_kill_switch(self, on: bool) -> None:
        with self._lock:
            self.kill_switch = bool(on)
            self._save()

    def is_enabled(self, tool: str) -> bool:
        return tool not in self.disabled

    def set_enabled(self, tool: str, enabled: bool) -> None:
        with self._lock:
            if enabled:
                self.disabled.discard(tool)
            else:
                self.disabled.add(tool)
            self._save()

    def enable_all(self) -> None:
        with self._lock:
            self.disabled.clear()
            self._save()

    def gate(self, tool: str) -> str | None:
        """Why this tool may not run right now, or None if it may.

        Checked before anything else in the executor, so it also stops safe tools and remembered
        grants. A denial here is final: the model is told to stop, not to find another way.
        """
        self._refresh()
        if self.kill_switch:
            return "Fox's kill switch is on. The user has stopped all actions. Do not try to work around it."
        if tool in self.disabled:
            return f"The tool '{tool}' has been turned off by the user and is unavailable. Do not try to work around it."
        return None

    def to_json(self) -> dict:
        return {"kill_switch": self.kill_switch, "disabled": sorted(self.disabled)}
