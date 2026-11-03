"""Local API for the GUI: REST for settings and history, a WebSocket for chat and permission prompts.

Fox can control this computer, so this server only listens on 127.0.0.1, and every request needs
the token in data/api_token.txt. Without that, any web page you visit could try to talk to it.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import importlib.util
import json
import logging
import re
import secrets
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Awaitable, Callable, Coroutine, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, ValidationError

from fox import __version__
from fox.config import ROOT, Settings
from fox.core.agents import AgentDef
from fox.core.events import Event, noop_emit
from fox.core.models import (
    CUSTOM_PROVIDERS,
    PROVIDERS,
    CustomModel,
    available_models,
    custom_models,
    is_configured,
    model_store,
    parse_chain,
    text_of,
)
from fox.core.permissions import ANY_SCOPE, Decision, PermissionRequest
from fox.core.tooling import REGISTRY
from fox.core.uploads import (
    UploadError,
    attachment_ids,
    clear_uploads,
    delete_upload,
    load_upload,
    save_upload,
    upload_file,
)
from fox.env_file import PLAIN_KEYS, SECRET_KEYS, update_env
from fox.runtime import _BUILTINS, Fox
from fox.tools.network import MAX_BATCH, capture_stats, ingest

log = logging.getLogger("fox.server")
HOST = "127.0.0.1"
STATIC_DIR = Path(__file__).parent / "gui" / "static"

# The page shows text that web pages and emails can influence, and it holds the API token.
# Script from anywhere else must never run in it, or a page could approve its own permission prompts.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def load_token(settings: Settings) -> str:
    path = settings.fox_data_dir / "api_token.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    return token


def voice_problem() -> str | None:
    """Why voice can't work, or None if its libraries are installed."""
    missing = [name for name in ("numpy", "sounddevice", "miniaudio") if importlib.util.find_spec(name) is None]
    return f"Voice libraries are missing: {', '.join(missing)}. Run: uv sync --all-extras" if missing else None


def env_text(value: object) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


class Hub:
    """Connected GUI windows. Also the approver: prompts go to all of them, and the first answer wins."""

    def __init__(self, timeout: float = 120, on_attention: Callable[[], None] | None = None) -> None:
        self.clients: set[WebSocket] = set()
        self.pending: dict[str, tuple[PermissionRequest, asyncio.Future[Decision]]] = {}
        # Running chat/voice turns, so the kill switch can stop whatever is in flight at once.
        self.turns: set[asyncio.Task] = set()
        self.timeout = timeout
        self.on_attention = on_attention
        # Set during a voice turn, so a prompt can be answered out loud as well as on screen.
        self.spoken: Callable[[PermissionRequest], Awaitable[Decision | None]] | None = None

    async def broadcast(self, payload: dict) -> int:
        delivered = 0
        for client in list(self.clients):
            try:
                await client.send_json(payload)
                delivered += 1
            except Exception:
                self.clients.discard(client)
        return delivered

    def attention(self) -> None:
        if self.on_attention:
            self.on_attention()

    def cancel_turns(self) -> int:
        """Stop every running turn. Used by the kill switch."""
        live = [task for task in self.turns if not task.done()]
        for task in live:
            task.cancel()
        return len(live)

    async def request(self, req: PermissionRequest) -> Decision:
        future: asyncio.Future[Decision] = asyncio.get_running_loop().create_future()
        self.pending[req.id] = (req, future)
        spoken: asyncio.Task | None = None

        def use_spoken_answer(task: asyncio.Task) -> None:
            # An unclear spoken answer leaves the prompt open on screen.
            if not task.cancelled() and not task.exception() and task.result() and not future.done():
                future.set_result(task.result())

        try:
            if not await self.broadcast({"type": "permission_request", **req.to_json(), "timeout": self.timeout}):
                return Decision.DENY
            self.attention()
            if self.spoken:
                spoken = asyncio.create_task(self.spoken(req))
                spoken.add_done_callback(use_spoken_answer)
            return await future
        finally:
            self.pending.pop(req.id, None)
            if spoken:
                spoken.cancel()
            # Without this, a prompt that timed out would stay on screen with buttons that do nothing.
            await self.broadcast({"type": "permission_closed", "id": req.id})

    def answer(self, request_id: str, value: str) -> None:
        req, future = self.pending.get(request_id, (None, None))
        if req and not future.done():
            try:
                decision = Decision(value)
            except ValueError:
                decision = Decision.DENY
            # Only what this prompt offered counts, so a critical action can't be answered with "always".
            future.set_result(decision if decision in req.options else Decision.DENY)


class AgentBody(BaseModel):
    description: str
    system_prompt: str
    tools: list[str] = []
    models: list[str] = []
    max_steps: int = 10


class GrantBody(BaseModel):
    tool: str
    scope: str = ANY_SCOPE


class SettingsBody(BaseModel):
    values: dict[str, str]


class PrefsBody(BaseModel):
    theme: Literal["system", "light", "dark"] = "system"
    speak: bool = False
    model: str = Field(default="", max_length=100)  # "" lets Fox use the fallback order


class KillBody(BaseModel):
    on: bool


class ToolToggleBody(BaseModel):
    tool: str
    enabled: bool


# Same rules as an agent name, so a plugin can only ever be a single file inside the plugins folder.
PLUGIN_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


class PluginBody(BaseModel):
    code: str = Field(min_length=1, max_length=100_000)


class ResetBody(BaseModel):
    # Each scope wipes one kind of stored state. Nothing is removed unless its scope is listed.
    scopes: list[Literal["conversations", "memory", "permissions", "audit", "tools", "captures", "cache"]] = []


class UploadBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    data: str  # base64


class CaptureBody(BaseModel):
    """What a browser extension sends. Each entry is checked and cleaned on its own."""

    entries: list[dict] = Field(max_length=MAX_BATCH)
    source: str = Field(default="extension", max_length=60)


class ModelBody(BaseModel):
    provider: str
    model: str
    base_url: str = ""
    api_key: str | None = None  # None keeps the saved key
    vision: bool = False


class ModelTestBody(BaseModel):
    model: str = Field(min_length=1, max_length=100)


def create_app(
    fox: Fox | None = None,
    token: str | None = None,
    port: int | None = None,
    on_attention: Callable[[], None] | None = None,
    env_path: Path | None = None,
) -> FastAPI:
    runtime = fox or Fox()
    settings = runtime.settings
    hub = Hub(settings.fox_permission_timeout, on_attention)
    runtime.permissions.approver = hub
    api_token = token or load_token(settings)
    env_file = env_path or ROOT / ".env"
    origins = [o.strip() for o in settings.fox_allowed_origins.split(",") if o.strip()]
    origins.append(f"http://{HOST}:{port or settings.fox_server_port}")  # the app's own page
    voice_service = None

    def get_voice():
        nonlocal voice_service
        if voice_service is None:
            from fox.voice.service import VoiceService

            voice_service = VoiceService(runtime)
        return voice_service

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async def on_reminder(reminder: dict) -> None:
            hub.attention()
            await hub.broadcast({"type": "reminder", **reminder})

        runtime.reminder_listeners.append(on_reminder)
        await runtime.start()
        yield
        await runtime.stop()

    # The interactive API docs load scripts from the internet, and nothing here needs them.
    app = FastAPI(title="Fox", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def require_token(authorization: str = Header(default="")) -> None:
        if not secrets.compare_digest(authorization.encode(), f"Bearer {api_token}".encode()):
            raise HTTPException(status_code=401, detail="Invalid or missing token")

    auth = [Depends(require_token)]

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/")
    def index() -> FileResponse:
        # The page holds no secrets. It gets the token from the desktop app, after the # in its address.
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/status", dependencies=auth)
    def status() -> dict:
        return {
            "version": __version__,
            "models": [s.label for s in runtime.router.specs],
            "available_models": available_models(runtime.settings),
            "tools": len(REGISTRY),
            "tools_on": len(runtime.enabled_tools()),
            "agents": [a.name for a in runtime.agents.list()],
            "clients": len(hub.clients),
            "voice_problem": voice_problem(),
            "wake_word": runtime.settings.fox_wake_word,
            "kill_switch": runtime.control.kill_switch,
        }

    @app.get("/api/tools", dependencies=auth)
    def tools() -> list[dict]:
        return [
            {
                "name": name,
                "risk": "varies" if callable(spec.risk) else spec.risk.value,
                "description": spec.tool.description,
                "enabled": runtime.control.is_enabled(name),
                "builtin": name in _BUILTINS,
            }
            for name, spec in sorted(REGISTRY.items())
        ]

    @app.get("/api/sessions", dependencies=auth)
    def sessions() -> list[dict]:
        return runtime.store.list_sessions()

    @app.get("/api/sessions/{session_id}/messages", dependencies=auth)
    def session_messages(session_id: str) -> list[dict]:
        return [
            {
                "role": m.type,
                # Attached documents are part of the stored message, but the window shows what was typed.
                "content": m.additional_kwargs.get("fox_text", text_of(m.content)),
                "attachments": m.additional_kwargs.get("fox_attachments", []),
                "tool": m.name if m.type == "tool" else None,
            }
            for m in runtime.store.load_messages(session_id)
        ]

    @app.delete("/api/sessions/{session_id}", dependencies=auth)
    def delete_session(session_id: str) -> dict:
        for upload_id in attachment_ids(runtime.store.load_messages(session_id)):
            delete_upload(runtime.settings, upload_id)
        runtime.store.delete_session(session_id)
        return {"ok": True}

    # --- Attachments ---

    @app.post("/api/uploads", dependencies=auth)
    def upload(body: UploadBody) -> dict:
        try:
            data = base64.b64decode(body.data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail="The file data isn't valid base64.") from exc
        try:
            return save_upload(runtime.settings, body.name, data)
        except UploadError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/uploads/{upload_id}", dependencies=auth)
    def upload_info(upload_id: str) -> dict:
        meta = load_upload(runtime.settings, upload_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="No such attachment")
        return meta

    @app.get("/api/uploads/{upload_id}/image", dependencies=auth)
    def upload_image(upload_id: str) -> Response:
        meta, path = load_upload(runtime.settings, upload_id), upload_file(runtime.settings, upload_id)
        if meta is None or path is None or meta["kind"] != "image":
            raise HTTPException(status_code=404, detail="No such image")
        return Response(path.read_bytes(), media_type=meta["mime"])

    @app.delete("/api/uploads/{upload_id}", dependencies=auth)
    def remove_upload(upload_id: str) -> dict:
        return {"deleted": delete_upload(runtime.settings, upload_id)}

    # --- Network captures from a browser extension ---

    @app.post("/api/captures", dependencies=auth)
    async def add_captures(body: CaptureBody) -> dict:
        stored, skipped = await asyncio.to_thread(ingest, body.entries, body.source)
        stats = capture_stats()
        await hub.broadcast({"type": "captures", **stats})
        return {"stored": stored, "skipped": skipped, **stats}

    @app.get("/api/captures", dependencies=auth)
    def list_captures(url_contains: str = "", limit: int = 100) -> dict:
        return {**capture_stats(), "entries": runtime.store.list_captures(url_contains, limit=max(1, min(limit, 1000)))}

    @app.delete("/api/captures", dependencies=auth)
    async def clear_captures() -> dict:
        runtime.store.clear_captures()
        stats = capture_stats()
        await hub.broadcast({"type": "captures", **stats})
        return stats

    # --- Models ---

    def models_view() -> dict:
        current = runtime.settings
        return {
            "chain": [spec.label for spec in runtime.router.specs],
            "available": available_models(current),
            "custom": [m.public(current) for m in custom_models(current).values()],
            "providers": {name: is_configured(current, name) for name in PROVIDERS},
            "custom_providers": CUSTOM_PROVIDERS,
        }

    @app.get("/api/models", dependencies=auth)
    def models() -> dict:
        return models_view()

    @app.put("/api/models/{name}", dependencies=auth)
    def save_model(name: str, body: ModelBody) -> dict:
        existing = custom_models(runtime.settings).get(name)
        key = body.api_key if body.api_key is not None else existing.api_key if existing else ""
        try:
            model = CustomModel(name=name, **body.model_dump(exclude={"api_key"}), api_key=key)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'model'}: {e['msg']}" for e in exc.errors())
            raise HTTPException(status_code=422, detail=problems) from exc
        if model.provider == "openai_compatible" and not model.base_url:
            raise HTTPException(status_code=422, detail="An OpenAI-compatible model needs its server address.")
        model_store(runtime.settings).save(model)
        runtime.router = runtime.make_router()  # the fallback order may name this model
        return models_view()

    @app.delete("/api/models/{name}", dependencies=auth)
    def delete_model(name: str) -> dict:
        model_store(runtime.settings).delete(name)
        runtime.router = runtime.make_router()
        return models_view()

    @app.post("/api/models/test", dependencies=auth)
    async def test_model(body: ModelTestBody) -> dict:
        specs = parse_chain(runtime.settings, [body.model])
        if not specs:
            raise HTTPException(status_code=400, detail=f"'{body.model}' isn't set up: it needs a key or an address.")
        router = runtime.make_router([body.model])
        started = time.monotonic()
        try:
            reply = await asyncio.wait_for(
                router.stream([HumanMessage("Reply with exactly one word: OK")], [], noop_emit), 90
            )
        except Exception as exc:
            return {"ok": False, "model": specs[0].label, "error": f"{type(exc).__name__}: {str(exc)[:600]}"}
        return {
            "ok": True,
            "model": specs[0].label,
            "reply": text_of(reply.content)[:200],
            "seconds": round(time.monotonic() - started, 2),
        }

    @app.get("/api/agents", dependencies=auth)
    def agents() -> list[dict]:
        return [a.model_dump() for a in runtime.agents.list()]

    @app.put("/api/agents/{name}", dependencies=auth)
    def save_agent(name: str, body: AgentBody) -> dict:
        try:
            definition = AgentDef(name=name, **body.model_dump())
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        runtime.agents.save(definition)
        return definition.model_dump()

    @app.delete("/api/agents/{name}", dependencies=auth)
    def delete_agent(name: str) -> dict:
        try:
            return {"deleted": runtime.agents.delete(name)}
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="Invalid agent name") from exc

    @app.get("/api/permissions", dependencies=auth)
    def permissions() -> dict:
        return runtime.permissions.list_rules()

    @app.post("/api/permissions/grant", dependencies=auth)
    def grant(body: GrantBody) -> dict:
        spec = REGISTRY.get(body.tool)
        if spec is None:
            raise HTTPException(status_code=404, detail="No such tool")
        if not callable(spec.risk) and spec.risk.value == "critical":
            raise HTTPException(status_code=400, detail="Critical tools always ask and can't be granted")
        runtime.permissions.grant(body.tool, body.scope)
        return runtime.permissions.list_rules()

    @app.post("/api/permissions/revoke", dependencies=auth)
    def revoke(body: GrantBody) -> dict:
        runtime.permissions.revoke(body.tool, body.scope)
        return runtime.permissions.list_rules()

    @app.get("/api/audit", dependencies=auth)
    def audit(limit: int = 100) -> list[dict]:
        return runtime.store.audit_log(limit)

    @app.get("/api/reminders", dependencies=auth)
    def reminders() -> list[dict]:
        return runtime.store.pending_reminders()

    @app.delete("/api/reminders/{reminder_id}", dependencies=auth)
    def delete_reminder(reminder_id: int) -> dict:
        runtime.store.delete_reminder(reminder_id)
        return {"ok": True}

    @app.get("/api/facts", dependencies=auth)
    def facts() -> list[dict]:
        return runtime.store.recent_facts(200)

    @app.delete("/api/facts/{fact_id}", dependencies=auth)
    def delete_fact(fact_id: int) -> dict:
        runtime.store.delete_fact(fact_id)
        return {"ok": True}

    # --- Live controls: the kill switch, turning tools on and off, and resetting stored state ---

    async def announce_control() -> None:
        # So every open window shows the same kill switch and tool state at once.
        await hub.broadcast({"type": "control", **runtime.control.to_json()})

    @app.get("/api/control", dependencies=auth)
    def control() -> dict:
        return runtime.control.to_json()

    @app.post("/api/control/kill", dependencies=auth)
    async def set_kill(body: KillBody) -> dict:
        runtime.control.set_kill_switch(body.on)
        stopped = hub.cancel_turns() if body.on else 0
        log.warning("Kill switch %s (stopped %d running turn(s))", "ON" if body.on else "off", stopped)
        await announce_control()
        return {**runtime.control.to_json(), "stopped": stopped}

    @app.post("/api/control/tools", dependencies=auth)
    async def toggle_tool(body: ToolToggleBody) -> dict:
        if body.tool not in REGISTRY:
            raise HTTPException(status_code=404, detail="No such tool")
        runtime.control.set_enabled(body.tool, body.enabled)
        await announce_control()
        return runtime.control.to_json()

    @app.get("/api/plugins", dependencies=auth)
    def plugins() -> list[dict]:
        # The load result for each file, so the GUI can show which tools it added or why it failed.
        return runtime.plugin_status

    def plugin_path(name: str) -> Path:
        if not PLUGIN_NAME.match(name):
            raise HTTPException(status_code=422, detail="Use lowercase letters, numbers, - and _ only.")
        return runtime.settings.fox_plugins_dir / f"{name}.py"

    @app.get("/api/plugins/{name}", dependencies=auth)
    def read_plugin(name: str) -> dict:
        path = plugin_path(name)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="No such plugin")
        return {"name": name, "code": path.read_text(encoding="utf-8")}

    @app.put("/api/plugins/{name}", dependencies=auth)
    def write_plugin(name: str, body: PluginBody) -> dict:
        path = plugin_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.code, encoding="utf-8")
        runtime.reload_tools()
        result = next((p for p in runtime.plugin_status if p["file"] == path.name), None)
        if result and not result["ok"]:
            # Keep the file so the user can fix it, but tell them it didn't load.
            raise HTTPException(status_code=422, detail=f"Saved, but it failed to load: {result['error']}")
        return result or {"file": path.name, "ok": True, "tools": [], "error": None}

    @app.delete("/api/plugins/{name}", dependencies=auth)
    def delete_plugin(name: str) -> dict:
        path = plugin_path(name)
        existed = path.exists()
        path.unlink(missing_ok=True)
        runtime.reload_tools()
        return {"deleted": existed}

    @app.post("/api/reset", dependencies=auth)
    async def reset(body: ResetBody) -> dict:
        done = []
        if "conversations" in body.scopes:
            runtime.store.clear_sessions()
            clear_uploads(runtime.settings)
            done.append("conversations")
        if "memory" in body.scopes:
            runtime.store.clear_facts()
            runtime.store.clear_reminders()
            done.append("memory")
        if "permissions" in body.scopes:
            runtime.permissions.clear_all()
            done.append("permissions")
        if "audit" in body.scopes:
            runtime.store.clear_audit()
            done.append("audit")
        if "captures" in body.scopes:
            runtime.store.clear_captures()
            done.append("captures")
        if "cache" in body.scopes:
            runtime.store.clear_answers()
            done.append("cache")
        if "tools" in body.scopes:
            runtime.control.enable_all()
            done.append("tools")
            await announce_control()
        log.warning("Reset performed: %s", ", ".join(done) or "nothing")
        return {"reset": done}

    def settings_view() -> dict:
        current = runtime.settings
        return {
            "path": str(env_file),
            "values": {key: env_text(getattr(current, key.lower())) for key in PLAIN_KEYS},
            # Only whether a secret is set. Its value never leaves the file.
            "secrets": {key: bool(getattr(current, key.lower())) for key in SECRET_KEYS},
            "providers": {name: is_configured(current, name) for name in PROVIDERS},
        }

    @app.get("/api/settings", dependencies=auth)
    def read_settings() -> dict:
        return settings_view()

    @app.put("/api/settings", dependencies=auth)
    def write_settings(body: SettingsBody) -> dict:
        nonlocal voice_service
        try:
            # Checked first: a value of the wrong type in .env would stop Fox from starting.
            Settings(**{key.lower(): value for key, value in body.values.items()})
            update_env(env_file, body.values)
        except ValidationError as exc:
            problems = "; ".join(f"{str(e['loc'][0]).upper()}: {e['msg']}" for e in exc.errors())
            raise HTTPException(status_code=422, detail=problems) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        runtime.apply_settings()
        hub.timeout = runtime.settings.fox_permission_timeout
        voice_service = None
        return settings_view()

    @app.post("/api/open/{target}", dependencies=auth)
    def open_file(target: Literal["env", "log"]) -> dict:
        path = env_file if target == "env" else runtime.settings.fox_data_dir / "fox.log"
        path.touch()
        subprocess.Popen(["notepad.exe", str(path)])
        return {"ok": True}

    def prefs_path() -> Path:
        return runtime.settings.fox_data_dir / "gui.json"

    @app.get("/api/prefs", dependencies=auth)
    def read_prefs() -> dict:
        try:
            return PrefsBody.model_validate(json.loads(prefs_path().read_text(encoding="utf-8"))).model_dump()
        except (OSError, ValueError):
            return PrefsBody().model_dump()

    @app.put("/api/prefs", dependencies=auth)
    def write_prefs(body: PrefsBody) -> dict:
        prefs_path().write_text(body.model_dump_json(), encoding="utf-8")
        return body.model_dump()

    @app.post("/api/voice/test", dependencies=auth)
    async def voice_test() -> dict:
        problem = voice_problem()
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        voice = await asyncio.to_thread(get_voice)
        try:
            await asyncio.to_thread(voice.speaker.say, "This is how Fox sounds.")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        supplied = ws.query_params.get("token", "")
        if (origin and origin not in origins) or not secrets.compare_digest(supplied.encode(), api_token.encode()):
            await ws.close(code=4401)
            return
        await ws.accept()
        hub.clients.add(ws)
        turn: asyncio.Task | None = None

        def begin(work: Coroutine) -> asyncio.Task:
            # Track the task so the kill switch can cancel it, and stop tracking it when it ends.
            task = asyncio.create_task(supervise(work))
            hub.turns.add(task)
            task.add_done_callback(hub.turns.discard)
            return task

        async def send_quietly(payload: dict) -> None:
            # The window may already be gone, and then there's nobody left to tell.
            try:
                await ws.send_json(payload)
            except Exception:
                pass

        async def send(event: Event) -> None:
            await send_quietly(event.to_json())

        async def supervise(work: Coroutine) -> None:
            try:
                await work
            except asyncio.CancelledError:
                await send_quietly({"type": "cancelled"})
                raise
            except Exception as exc:
                log.exception("Chat turn failed")
                await send_quietly({"type": "error", "message": str(exc)})
            finally:
                # Spoken replies outlast "done", so this is what says Fox is free again.
                await send_quietly({"type": "turn_end"})

        async def typed_turn(text: str, session_id: str | None, speak: bool, **options) -> None:
            if not speak or voice_problem():
                await runtime.chat(text, session_id, send, **options)
                return
            voice = await asyncio.to_thread(get_voice)
            try:
                await voice.reply(text, session_id, send, **options)
            finally:
                await send(Event("voice_state", {"state": "idle"}))

        async def voice_turn(wake: bool, session_id: str | None) -> None:
            problem = voice_problem()
            if problem:
                raise RuntimeError(problem)
            voice = await asyncio.to_thread(get_voice)
            hub.spoken = voice.ask
            try:
                await voice.conversation(wake, session_id, send)
            finally:
                hub.spoken = None

        try:
            while True:
                message = await ws.receive_json()
                kind = message.get("type")
                if kind in ("message", "voice_start") and turn and not turn.done():
                    await ws.send_json({"type": "error", "message": "Fox is still working on the last message."})
                elif kind == "message":
                    attachments = message.get("attachments") or []
                    work = typed_turn(
                        str(message.get("text", "")),
                        message.get("session_id"),
                        bool(message.get("speak")),
                        attachments=[str(a) for a in attachments] if isinstance(attachments, list) else [],
                        model=str(message.get("model") or "") or None,
                    )
                    turn = begin(work)
                elif kind == "voice_start":
                    turn = begin(voice_turn(bool(message.get("wake")), message.get("session_id")))
                elif kind == "permission_response":
                    hub.answer(str(message.get("id")), str(message.get("decision")))
                elif kind == "cancel" and turn:
                    turn.cancel()
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            hub.clients.discard(ws)
            if turn and not turn.done():
                turn.cancel()

    return app
