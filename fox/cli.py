"""The `fox` command: chat, voice, serve, doctor, agents and permissions."""

from __future__ import annotations

import asyncio
import shlex
import sys

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fox.config import ROOT, get_settings
from fox.core.events import Event
from fox.core.models import PROVIDERS, custom_models, custom_ready, is_configured, parse_chain
from fox.core.permissions import ANY_SCOPE, Decision, PermissionRequest, Risk

app = typer.Typer(help="Fox: your permission-first personal assistant.", no_args_is_help=True, add_completion=False)
agents_app = typer.Typer(help="Manage specialist agents.", no_args_is_help=True)
permissions_app = typer.Typer(help="View and change remembered permissions.", no_args_is_help=True)
app.add_typer(agents_app, name="agents")
app.add_typer(permissions_app, name="permissions")

console = Console()
CHAT_HELP = """Commands:
  /new                     start a new conversation
  /models                  show the model fallback chain
  /tools                   show all tools and their risk level
  /agents                  list specialist agents
  /permissions             list remembered permissions
  /grant TOOL [TARGET]     always allow a tool, or just one target
  /revoke TOOL [TARGET]    remove a remembered permission
  /exit                    quit"""


async def ask_line(prompt: str) -> str:
    if sys.stdin.isatty():
        from prompt_toolkit import PromptSession

        return await PromptSession().prompt_async(prompt)
    return await asyncio.to_thread(input, prompt)


def parse_terminal_decision(answer: str, risk: Risk) -> Decision:
    """Anything unexpected is a refusal. A critical action needs the whole word 'yes'."""
    answer = answer.strip().lower()
    if answer in ("x", "never"):
        return Decision.NEVER
    if risk is Risk.CRITICAL:
        return Decision.ONCE if answer == "yes" else Decision.DENY
    return {
        "y": Decision.ONCE,
        "yes": Decision.ONCE,
        "s": Decision.SESSION,
        "session": Decision.SESSION,
        "a": Decision.ALWAYS,
        "always": Decision.ALWAYS,
    }.get(answer, Decision.DENY)


class TerminalApprover:
    async def request(self, req: PermissionRequest) -> Decision:
        critical = req.risk is Risk.CRITICAL
        by = "" if req.agent == "fox" else f" (asked by agent {req.agent})"
        console.print()
        console.print(
            Panel(
                Text(req.description),
                title=f"{'CRITICAL: ' if critical else ''}Permission needed: {req.tool}{by}",
                border_style="red" if critical else "yellow",
            )
        )
        if critical:
            prompt = "Type yes to allow once, n to refuse, x to always refuse: "
        else:
            prompt = f"[y] once  [s] this session  [a] always for '{req.scope}'  [n] no  [x] never: "
        try:
            return parse_terminal_decision(await ask_line(prompt), req.risk)
        except (EOFError, KeyboardInterrupt):
            return Decision.DENY


async def render_event(event: Event) -> None:
    if event.type == "text":
        console.print(event.data["delta"], end="", markup=False, highlight=False)
    elif event.type == "text_reset":
        console.print("\n[dim](that model failed partway, starting the answer over)[/dim]")
    elif event.type == "model_switch":
        console.print(f"\n[yellow]Switching to {event.data['model']}[/yellow] [dim]{event.data['reason']}[/dim]", highlight=False)
    elif event.type == "tool_start":
        console.print(f"\n[dim]> {event.data['agent']}: {event.data['summary']}[/dim]", markup=True, highlight=False)
    elif event.type == "tool_end" and not event.data["ok"]:
        console.print(Text(f"  {event.data['output'][:200]}", style="red"))
    elif event.type == "agent_start":
        console.print(f"\n[cyan]Handing off to agent {event.data['agent']}[/cyan]", highlight=False)


def print_tools() -> None:
    from fox.core.tooling import REGISTRY

    table = Table("Tool", "Risk", "What it does")
    for name, spec in sorted(REGISTRY.items()):
        risk = "varies" if callable(spec.risk) else spec.risk.value
        table.add_row(name, risk, (spec.tool.description or "").splitlines()[0])
    console.print(table)


def print_permissions(fox) -> None:
    rules = fox.permissions.list_rules()
    table = Table("Rule", "Tool", "Target")
    for kind, label in (("allow", "always allow"), ("session", "this session"), ("deny", "never")):
        for rule in rules[kind]:
            table.add_row(label, rule["tool"], rule["scope"])
    console.print(table if table.row_count else "No remembered permissions. Fox asks every time.")


def grant_permission(fox, tool: str, scope: str) -> None:
    from fox.core.tooling import REGISTRY

    spec = REGISTRY.get(tool)
    if spec is None:
        console.print(f"[red]No tool named {tool}.[/red]")
    elif spec.risk is Risk.CRITICAL:
        console.print(f"[red]{tool} is critical, so Fox asks every time. It can't be granted.[/red]")
    else:
        fox.permissions.grant(tool, scope)
        extra = " Calls that count as critical will still ask." if callable(spec.risk) else ""
        console.print(f"Granted {tool} for {scope}.{extra}")


async def handle_command(fox, line: str) -> str | None:
    """Run a slash command. Returns 'exit' or 'new' when the chat loop must act."""
    parts = shlex.split(line, posix=False)
    command, args = parts[0].lower(), [p.strip('"') for p in parts[1:]]
    if command in ("/exit", "/quit"):
        return "exit"
    if command == "/new":
        return "new"
    if command == "/models":
        console.print(", ".join(s.label for s in fox.router.specs) or "[red]No model configured.[/red]")
    elif command == "/tools":
        print_tools()
    elif command == "/agents":
        for agent in fox.agents.list():
            console.print(f"[bold]{agent.name}[/bold]: {agent.description}", highlight=False)
    elif command == "/permissions":
        print_permissions(fox)
    elif command == "/grant" and args:
        grant_permission(fox, args[0], args[1] if len(args) > 1 else ANY_SCOPE)
    elif command == "/revoke" and args:
        removed = fox.permissions.revoke(args[0], args[1] if len(args) > 1 else ANY_SCOPE)
        console.print("Removed." if removed else "No matching rule.")
    else:
        console.print(CHAT_HELP, markup=False)
    return None


async def chat_loop(session_id: str | None) -> None:
    from fox.runtime import Fox

    fox = Fox(approver=TerminalApprover())
    await fox.start()
    labels = ", ".join(s.label for s in fox.router.specs)
    console.print(f"[bold]Fox[/bold] [dim]models: {labels or 'none configured'}[/dim]  Type /help for commands.", highlight=False)
    if not labels:
        console.print("[red]No model is configured. Add a key to .env, then run: fox doctor[/red]")
    try:
        while True:
            console.print()
            try:
                line = (await ask_line("You: ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line.startswith("/"):
                action = await handle_command(fox, line)
                if action == "exit":
                    break
                if action == "new":
                    session_id = None
                    console.print("[dim]New conversation.[/dim]")
                continue

            console.print("[bold green]Fox:[/bold green] ", end="")
            session_id = session_id or fox.store.create_session(line)
            try:
                await asyncio.create_task(fox.chat(line, session_id, render_event))
            except asyncio.CancelledError:
                console.print("\n[dim]Stopped.[/dim]")
            except Exception as exc:
                console.print(Text(f"\n{exc}", style="red"))
            console.print()
    finally:
        await fox.stop()


@app.command()
def chat(session: str = typer.Option(None, help="Resume a conversation by its id.")) -> None:
    """Chat with Fox in the terminal."""
    try:
        asyncio.run(chat_loop(session))
    except KeyboardInterrupt:
        pass


@app.command()
def voice(wake: bool = typer.Option(False, help="Always listen for the wake word instead of pressing Enter.")) -> None:
    """Talk to Fox out loud."""
    from fox.voice.loop import voice_loop

    try:
        asyncio.run(voice_loop(wake))
    except KeyboardInterrupt:
        pass


@app.command()
def say(text: str) -> None:
    """Speak a sentence, to test your voice settings."""
    from fox.voice.tts import Speaker

    Speaker(get_settings()).say(text)


@app.command()
def gui() -> None:
    """Open the desktop app."""
    from fox.gui.app import main

    main()


@app.command()
def serve() -> None:
    """Run the local API without the desktop app. Only this computer can reach it."""
    import uvicorn

    from fox.server import HOST, create_app, load_token

    settings = get_settings()
    console.print(f"Fox API on http://{HOST}:{settings.fox_server_port}")
    console.print(f"API token is in {settings.fox_data_dir / 'api_token.txt'} (created on first run)")
    load_token(settings)
    uvicorn.run(create_app(), host=HOST, port=settings.fox_server_port, log_level="warning")


@app.command()
def doctor() -> None:
    """Check configuration and which features are ready."""
    import importlib.util

    from fox.runtime import Fox

    s = get_settings()
    env_path = ROOT / ".env"
    console.print(f".env file: {'found' if env_path.exists() else '[red]missing[/red]'}", highlight=False)

    table = Table("Model provider", "Key set", "Library installed")
    packages = {
        "azure": "langchain_openai", "openai": "langchain_openai", "gemini": "langchain_google_genai",
        "groq": "langchain_groq", "anthropic": "langchain_anthropic", "ollama": "langchain_ollama",
    }  # fmt: skip
    for provider in PROVIDERS:
        installed = importlib.util.find_spec(packages[provider]) is not None
        table.add_row(provider, "yes" if is_configured(s, provider) else "no", "yes" if installed else "no")
    console.print(table)
    added = custom_models(s)
    if added:
        mine = Table("Your model", "Provider", "Model id", "Reads images", "Ready")
        for m in added.values():
            mine.add_row(m.name, m.provider, m.model, "yes" if m.vision else "no", "yes" if custom_ready(s, m) else "no")
        console.print(mine)
    specs = parse_chain(s, s.fox_model_chain)
    console.print("Fallback order: " + (" -> ".join(x.label for x in specs) or "[red]no model configured[/red]"), highlight=False)
    console.print(f"Email: {'set' if s.email_address and s.email_password else 'not set'}")
    console.print(f"Voice output order: {s.fox_tts_chain}  (robot effect: {s.fox_tts_robot_effect})", highlight=False)
    console.print(f"Voice input order: {s.fox_stt_chain}", highlight=False)
    fox = Fox()
    console.print(f"Tools: {len(_tool_names())}  Agents: {len(fox.agents.list())}")


def _tool_names() -> list[str]:
    from fox.core.tooling import REGISTRY

    return list(REGISTRY)


@app.command()
def tools() -> None:
    """List every tool and its risk level."""
    from fox.runtime import Fox

    Fox()
    print_tools()


@app.command()
def kill(state: str = typer.Argument("status", help="on, off, or status.")) -> None:
    """Fox's kill switch. On blocks every tool until you turn it off."""
    from fox.core.control import Control

    control = Control(get_settings().fox_data_dir / "control.json")
    choice = state.strip().lower()
    if choice in ("on", "off"):
        control.set_kill_switch(choice == "on")
    elif choice != "status":
        console.print("[red]Use: fox kill on | off | status[/red]")
        raise typer.Exit(1)
    console.print(f"Kill switch is [bold]{'ON' if control.kill_switch else 'off'}[/bold].", highlight=False)
    if choice == "on":
        console.print("[yellow]A running Fox blocks its next tool call. Turn it off with: fox kill off[/yellow]")


@app.command()
def sessions() -> None:
    """List recent conversations."""
    from fox.core.storage import get_store

    table = Table("Id", "Title", "Updated")
    for row in get_store().list_sessions(20):
        table.add_row(row["id"], row["title"], row["updated"])
    console.print(table)


@agents_app.command("list")
def agents_list() -> None:
    """List specialist agents."""
    from fox.core.agents import AgentRegistry

    table = Table("Name", "Description", "Tools", "Models")
    for agent in AgentRegistry(get_settings().fox_agents_dir).list():
        table.add_row(agent.name, agent.description, ", ".join(agent.tools), ", ".join(agent.models) or "default")
    console.print(table)


@agents_app.command("add")
def agents_add(
    name: str,
    description: str = typer.Option(..., help="What the agent is for. Fox uses this to pick it."),
    prompt: str = typer.Option(..., help="The agent's instructions."),
    tools: str = typer.Option("", help="Comma-separated tool names, or * for all."),
    models: str = typer.Option("", help="Comma-separated fallback chain, such as groq,azure."),
) -> None:
    """Add a specialist agent."""
    from pydantic import ValidationError

    from fox.core.agents import AgentDef, AgentRegistry

    split = lambda value: [item.strip() for item in value.split(",") if item.strip()]  # noqa: E731
    try:
        definition = AgentDef(name=name, description=description, system_prompt=prompt, tools=split(tools), models=split(models))
    except ValidationError as exc:
        console.print(f"[red]Invalid agent: {exc}[/red]", markup=False)
        raise typer.Exit(1)
    console.print(f"Saved to {AgentRegistry(get_settings().fox_agents_dir).save(definition)}")


@agents_app.command("remove")
def agents_remove(name: str) -> None:
    """Remove a specialist agent."""
    from fox.core.agents import AgentRegistry

    removed = AgentRegistry(get_settings().fox_agents_dir).delete(name)
    console.print("Removed." if removed else "No agent with that name.")


@permissions_app.command("list")
def permissions_list() -> None:
    """List remembered permissions."""
    from fox.runtime import Fox

    print_permissions(Fox())


@permissions_app.command("grant")
def permissions_grant(tool: str, target: str = typer.Argument(ANY_SCOPE)) -> None:
    """Always allow a tool, or only one target of it, such as: grant open_app notepad."""
    from fox.runtime import Fox

    grant_permission(Fox(), tool, target)


@permissions_app.command("revoke")
def permissions_revoke(tool: str, target: str = typer.Argument(ANY_SCOPE)) -> None:
    """Remove a remembered permission."""
    from fox.runtime import Fox

    console.print("Removed." if Fox().permissions.revoke(tool, target) else "No matching rule.")


@permissions_app.command("audit")
def permissions_audit(limit: int = 30) -> None:
    """Show the log of permission decisions."""
    from fox.core.storage import get_store

    table = Table("When", "Agent", "Tool", "Outcome", "Action")
    for row in get_store().audit_log(limit):
        table.add_row(row["at"], row["agent"], row["tool"], row["outcome"], row["description"].splitlines()[0][:70])
    console.print(table)


if __name__ == "__main__":
    app()
