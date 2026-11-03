"""Tool registry and the executor that runs every tool call behind the permission gate."""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from fox.core.control import Control
from fox.core.events import Emit, Event
from fox.core.permissions import ANY_SCOPE, PermissionManager, Risk

log = logging.getLogger("fox.tools")


class ToolError(Exception):
    """An expected failure. The message goes back to the model as the tool result."""


@dataclass
class FoxTool:
    tool: StructuredTool
    risk: Risk | Callable[[dict], Risk]
    scope: Callable[[dict], str]
    describe: Callable[[dict], str]
    untrusted: bool
    timeout: float | None

    def risk_for(self, args: dict) -> Risk:
        return self.risk(args) if callable(self.risk) else self.risk


REGISTRY: dict[str, FoxTool] = {}
DEFAULT_TIMEOUT = -1.0  # replaced by FOX_TOOL_TIMEOUT when the tool runs


def fox_tool(
    risk: Risk | Callable[[dict], Risk] = Risk.NORMAL,
    scope: Callable[[dict], str] | None = None,
    describe: Callable[[dict], str] | None = None,
    untrusted: bool = False,
    timeout: float | None = DEFAULT_TIMEOUT,
):
    """Register a function as a tool. Its docstring is what the model reads.

    risk: Risk, or a function of the arguments when some calls are more dangerous.
    scope: what a remembered permission applies to, such as an app name or website host.
    describe: the text shown to the user when asking. Must reflect what will really happen.
    untrusted: the output comes from outside (web, email, files) and is marked as data, not instructions.
    timeout: seconds, or None for tools that wait on other tools.
    """

    def decorator(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):
            tool = StructuredTool.from_function(coroutine=fn, name=fn.__name__, description=inspect.getdoc(fn))
        else:
            tool = StructuredTool.from_function(func=fn, name=fn.__name__, description=inspect.getdoc(fn))
        name = fn.__name__
        REGISTRY[name] = FoxTool(
            tool=tool,
            risk=risk,
            scope=scope or (lambda args: ANY_SCOPE),
            describe=describe or (lambda args: f"Run {name}\n{json.dumps(args, indent=2, default=str)}"),
            untrusted=untrusted,
            timeout=timeout,
        )
        return fn

    return decorator


@dataclass
class RunContext:
    """What tools can see about the current run, such as the runtime for delegating to agents."""

    fox: Any
    emit: Emit


CURRENT_RUN: contextvars.ContextVar[RunContext] = contextvars.ContextVar("fox_current_run")


class ToolExecutor:
    def __init__(
        self,
        permissions: PermissionManager,
        tool_timeout: float = 120,
        max_output: int = 20000,
        control: Control | None = None,
    ):
        self.permissions = permissions
        self.tool_timeout = tool_timeout
        self.max_output = max_output
        # When absent, nothing is gated: every tool is on and the kill switch is off.
        self.control = control

    async def execute(self, name: str, args: dict, agent: str, allowed: set[str], emit: Emit) -> str:
        spec = REGISTRY.get(name)
        if spec is None or name not in allowed:
            return f"Error: tool '{name}' is not available to you."

        # The kill switch and turned-off tools come before everything, even safe tools and
        # remembered grants, so stopping Fox really stops it.
        blocked = self.control.gate(name) if self.control else None
        if blocked:
            await emit(Event("tool_end", {"tool": name, "agent": agent, "ok": False, "output": blocked}))
            return f"Blocked: {blocked}"

        # Validate first, so the user is never asked about a call that can't run as shown.
        try:
            clean = spec.tool.args_schema.model_validate(args).model_dump()
        except ValidationError as exc:
            return f"Error: invalid arguments for {name}: {exc}"

        try:
            risk = spec.risk_for(clean)
            scope = spec.scope(clean)
            description = spec.describe(clean)
        except ToolError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            log.exception("Could not prepare %s", name)
            return f"Error: could not prepare {name}: {exc}"

        allowed_to_run, reason = await self.permissions.authorize(name, scope, description, risk, agent)
        if not allowed_to_run:
            await emit(Event("tool_end", {"tool": name, "agent": agent, "ok": False, "output": reason}))
            return f"Permission denied: {reason}"

        await emit(Event("tool_start", {"tool": name, "agent": agent, "summary": description.splitlines()[0]}))
        ok = False
        timeout = self.tool_timeout if spec.timeout == DEFAULT_TIMEOUT else spec.timeout
        try:
            result = await asyncio.wait_for(spec.tool.ainvoke(clean), timeout)
            output = result if isinstance(result, str) else json.dumps(result, default=str)
            ok = True
        except asyncio.TimeoutError:
            output = f"Error: {name} timed out after {timeout} seconds. It may still have taken effect."
        except ToolError as exc:
            output = f"Error: {exc}"
        except Exception as exc:
            log.exception("Tool %s failed", name)
            output = f"Error: {name} failed: {exc}"

        if len(output) > self.max_output:
            output = output[: self.max_output] + f"\n[truncated, {len(output)} characters in total]"
        await emit(Event("tool_end", {"tool": name, "agent": agent, "ok": ok, "output": output[:500]}))

        if spec.untrusted and ok:
            return f'<untrusted_data source="{name}">\n{output}\n</untrusted_data>'
        return output
