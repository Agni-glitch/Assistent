"""The permission gate. Every tool call passes through here before it runs.

Rules:
- SAFE tools run without asking. They only read harmless state.
- NORMAL tools ask, and the answer can be remembered per target (or for the whole tool).
- CRITICAL tools ask every time. Session and always grants never apply to them.
- Anything that goes wrong while asking (timeout, closed window, no UI) counts as a denial.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from fox.core.storage import Store

log = logging.getLogger("fox.permissions")

ANY_SCOPE = "*"


class Risk(str, Enum):
    SAFE = "safe"
    NORMAL = "normal"
    CRITICAL = "critical"


class Decision(str, Enum):
    ONCE = "once"  # allow this call
    SESSION = "session"  # allow this target until Fox exits
    ALWAYS = "always"  # allow this target from now on
    DENY = "deny"  # refuse this call
    NEVER = "never"  # refuse this target from now on


@dataclass
class PermissionRequest:
    tool: str
    scope: str
    description: str  # first line is a short summary, the rest is full detail
    risk: Risk
    agent: str = "fox"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def summary(self) -> str:
        return self.description.splitlines()[0] if self.description else self.tool

    @property
    def options(self) -> list[Decision]:
        if self.risk is Risk.CRITICAL:
            return [Decision.ONCE, Decision.DENY, Decision.NEVER]
        return list(Decision)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "tool": self.tool,
            "scope": self.scope,
            "summary": self.summary,
            "description": self.description,
            "risk": self.risk.value,
            "agent": self.agent,
            "options": [o.value for o in self.options],
        }


class Approver(Protocol):
    async def request(self, req: PermissionRequest) -> Decision: ...


class DenyAllApprover:
    """Used when no interface is attached, so nothing can run unattended."""

    async def request(self, req: PermissionRequest) -> Decision:
        return Decision.DENY


class PermissionManager:
    def __init__(self, path: Path, store: Store, approver: Approver | None = None, timeout: float = 120):
        self.path = path
        self.store = store
        self.approver: Approver = approver or DenyAllApprover()
        self.timeout = timeout
        self._session_grants: set[tuple[str, str]] = set()
        self._lock = asyncio.Lock()
        self._rules = self._load()

    def _load(self) -> dict[str, list[list[str]]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {"allow": data.get("allow", []), "deny": data.get("deny", [])}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.error("Ignoring unreadable %s: %s", self.path, exc)
        return {"allow": [], "deny": []}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._rules, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _matches(rules, tool: str, scope: str) -> bool:
        return [tool, scope] in rules or [tool, ANY_SCOPE] in rules

    def _add_rule(self, kind: str, tool: str, scope: str) -> None:
        other = "deny" if kind == "allow" else "allow"
        if [tool, scope] in self._rules[other]:
            self._rules[other].remove([tool, scope])
        if [tool, scope] not in self._rules[kind]:
            self._rules[kind].append([tool, scope])
        self._save()

    # Called by the user through the CLI or GUI only. No tool can reach these.
    def grant(self, tool: str, scope: str = ANY_SCOPE) -> None:
        self._add_rule("allow", tool, scope.lower())

    def deny(self, tool: str, scope: str = ANY_SCOPE) -> None:
        self._add_rule("deny", tool, scope.lower())

    def revoke(self, tool: str, scope: str = ANY_SCOPE) -> bool:
        pair = [tool, scope.lower()]
        removed = False
        for kind in ("allow", "deny"):
            if pair in self._rules[kind]:
                self._rules[kind].remove(pair)
                removed = True
        if tuple(pair) in self._session_grants:
            self._session_grants.discard(tuple(pair))
            removed = True
        if removed:
            self._save()
        return removed

    def clear_all(self) -> None:
        """Forget every remembered allow, deny and session grant. Fox goes back to asking each time."""
        self._rules = {"allow": [], "deny": []}
        self._session_grants.clear()
        self._save()

    def list_rules(self) -> dict:
        return {
            "allow": [{"tool": t, "scope": s} for t, s in self._rules["allow"]],
            "deny": [{"tool": t, "scope": s} for t, s in self._rules["deny"]],
            "session": [{"tool": t, "scope": s} for t, s in sorted(self._session_grants)],
        }

    async def authorize(self, tool: str, scope: str, description: str, risk: Risk, agent: str = "fox") -> tuple[bool, str]:
        """Return (allowed, reason). The reason is safe to show to the model."""
        scope = (scope or ANY_SCOPE).lower()

        if risk is Risk.SAFE:
            return True, "safe"

        if self._matches(self._rules["deny"], tool, scope):
            self.store.audit(agent, tool, scope, risk.value, "blocked_by_rule", description)
            return False, "The user has permanently blocked this action. Do not retry it."

        if risk is Risk.NORMAL:
            remembered = (tool, scope) in self._session_grants or (tool, ANY_SCOPE) in self._session_grants
            if remembered or self._matches(self._rules["allow"], tool, scope):
                self.store.audit(agent, tool, scope, risk.value, "allowed_by_grant", description)
                return True, "remembered grant"

        req = PermissionRequest(tool=tool, scope=scope, description=description, risk=risk, agent=agent)
        decision = await self._ask(req)

        if decision is Decision.SESSION and risk is Risk.NORMAL:
            self._session_grants.add((tool, scope))
        elif decision is Decision.ALWAYS and risk is Risk.NORMAL:
            self._add_rule("allow", tool, scope)
        elif decision is Decision.NEVER:
            self._add_rule("deny", tool, scope)

        allowed = decision in (Decision.ONCE, Decision.SESSION, Decision.ALWAYS)
        self.store.audit(agent, tool, scope, risk.value, f"user_{decision.value}", description)
        if allowed:
            return True, decision.value
        return False, "The user declined this action. Do not retry it unless they ask again."

    async def _ask(self, req: PermissionRequest) -> Decision:
        # One prompt at a time, so answers can't be applied to the wrong question.
        async with self._lock:
            try:
                return await asyncio.wait_for(self.approver.request(req), self.timeout)
            except asyncio.TimeoutError:
                log.warning("Permission request timed out: %s", req.summary)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Approver failed for %s", req.tool)
            return Decision.DENY
