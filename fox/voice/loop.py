"""Voice mode: listen, think, speak, and ask for permission out loud."""

from __future__ import annotations

import asyncio
import re
import threading
import time

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from fox.core.events import Event
from fox.core.permissions import Decision, PermissionRequest, Risk
from fox.runtime import Fox
from fox.voice.audio import record_utterance
from fox.voice.stt import Transcriber
from fox.voice.tts import Speaker

FOLLOW_UP_SECONDS = 12
NEGATIVE = {"no", "nope", "not", "don't", "dont", "stop", "cancel", "deny", "denied", "wait", "never", "nah"}
POSITIVE = {"yes", "yeah", "yep", "yup", "sure", "okay", "ok", "allow", "approve", "approved", "go"}
EXIT_PHRASES = {"goodbye", "goodbye fox", "exit voice mode", "stop listening", "bye fox"}


def parse_voice_decision(heard: str, risk: Risk) -> Decision | None:
    """Turn a spoken answer into a decision. None means it wasn't clear.

    Any negative word wins, so "no, don't" and "yes, wait" both refuse. A critical action
    needs the word "confirm", so background chatter with a stray "yes" can't approve it.
    """
    words = set(re.findall(r"[a-z']+", heard.lower()))
    if "never" in words:
        return Decision.NEVER
    if words & NEGATIVE:
        return Decision.DENY
    if risk is Risk.CRITICAL:
        return Decision.ONCE if "confirm" in words else None
    if "always" in words:
        return Decision.ALWAYS
    if "session" in words:
        return Decision.SESSION
    if words & POSITIVE:
        return Decision.ONCE
    return None


def strip_wake_word(text: str, wake_word: str) -> str | None:
    """Return what follows the wake word, or None if it wasn't said."""
    match = re.search(rf"\b{re.escape(wake_word)}\b[\s,.!?:-]*(.*)", text, flags=re.I | re.S)
    return match.group(1).strip() if match else None


class VoiceApprover:
    def __init__(self, speaker: Speaker, transcriber: Transcriber, console: Console):
        self.speaker = speaker
        self.transcriber = transcriber
        self.console = console

    async def request(self, req: PermissionRequest) -> Decision:
        decision = await self.ask(req)
        if decision is not None:
            return decision
        await asyncio.to_thread(self.speaker.say, "I'll take that as a no.")
        return Decision.DENY

    async def ask(self, req: PermissionRequest, stop: threading.Event | None = None) -> Decision | None:
        """Ask out loud. Returns None if the answer stayed unclear."""
        style = "red" if req.risk is Risk.CRITICAL else "yellow"
        self.console.print(Panel(Text(req.description), title=f"Permission needed: {req.tool}", border_style=style))
        if req.risk is Risk.CRITICAL:
            question = f"Permission needed. {req.summary}. Say confirm to allow, or no to refuse."
        else:
            question = f"May I? {req.summary}. Say yes, always, or no."

        for attempt in range(2):
            await asyncio.to_thread(self.speaker.say, question if attempt == 0 else "Sorry, I didn't catch that.")
            audio = await asyncio.to_thread(record_utterance, 10.0, stop=stop)
            heard = await asyncio.to_thread(self.transcriber.transcribe, audio) if audio is not None else ""
            self.console.print(f"[dim]Heard: {heard or '(nothing)'}[/dim]", highlight=False)
            decision = parse_voice_decision(heard, req.risk)
            if decision is not None:
                return decision
        return None


class SentenceSpeaker:
    """Speaks a streaming reply sentence by sentence, so the first words come quickly."""

    def __init__(self, speaker: Speaker):
        self.speaker = speaker
        self.buffer = ""
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.worker = asyncio.create_task(self._work())

    async def _work(self) -> None:
        while (sentence := await self.queue.get()) is not None:
            try:
                await asyncio.to_thread(self.speaker.say, sentence)
            except Exception as exc:
                Console().print(f"[red]Voice output failed: {exc}[/red]")

    def feed(self, delta: str) -> None:
        self.buffer += delta
        parts = re.split(r"(?<=[.!?])\s+", self.buffer)
        self.buffer = parts.pop()
        for part in parts:
            self.queue.put_nowait(part)

    def reset(self) -> None:
        """Drop unspoken text, because another model is starting the answer over."""
        self.buffer = ""
        while not self.queue.empty():
            self.queue.get_nowait()

    async def finish(self) -> None:
        if self.buffer.strip():
            self.queue.put_nowait(self.buffer)
        self.buffer = ""
        self.queue.put_nowait(None)
        await self.worker


async def voice_loop(wake: bool = False) -> None:
    console = Console()
    fox = Fox()
    speaker = Speaker(fox.settings)
    transcriber = Transcriber(fox.settings)
    fox.permissions.approver = VoiceApprover(speaker, transcriber, console)
    await fox.start()

    wake_word = fox.settings.fox_wake_word
    session_id = None
    awake_until = 0.0
    if wake:
        console.print(f"[bold]Fox is listening.[/bold] Say '{wake_word}' to start. Say 'goodbye Fox' to stop. Press Ctrl+C to quit.")
    else:
        console.print("[bold]Fox voice mode.[/bold] Press Enter, then speak. Type q and press Enter to quit.")
    await asyncio.to_thread(speaker.say, "Fox online.")

    try:
        while True:
            if not wake:
                typed = await asyncio.to_thread(input, "[Enter to talk] ")
                if typed.strip().lower() in ("q", "quit", "exit"):
                    break
                console.print("[dim]Listening...[/dim]")

            audio = await asyncio.to_thread(record_utterance)
            if audio is None:
                continue
            heard = await asyncio.to_thread(transcriber.transcribe, audio)
            if not heard:
                continue

            if wake and time.monotonic() > awake_until:
                heard = strip_wake_word(heard, wake_word)
                if heard is None:
                    continue
                if not heard:
                    await asyncio.to_thread(speaker.say, "Yes?")
                    awake_until = time.monotonic() + FOLLOW_UP_SECONDS
                    continue

            console.print(f"[bold cyan]You:[/bold cyan] {heard}", highlight=False)
            if re.sub(r"[^a-z ]", "", heard.lower()).strip() in EXIT_PHRASES:
                await asyncio.to_thread(speaker.say, "Goodbye.")
                break

            voice_out = SentenceSpeaker(speaker)

            async def emit(event: Event) -> None:
                if event.type == "text":
                    console.print(event.data["delta"], end="", markup=False, highlight=False)
                    voice_out.feed(event.data["delta"])
                elif event.type == "text_reset":
                    console.print("\n[dim](switching models, starting over)[/dim]")
                    voice_out.reset()
                elif event.type == "tool_start":
                    console.print(f"\n[dim]> {event.data['summary']}[/dim]", highlight=False)

            session_id = session_id or fox.store.create_session(heard)
            try:
                await fox.chat(heard, session_id, emit, voice=True)
            except Exception as exc:
                console.print(f"[red]{exc}[/red]", markup=False)
                voice_out.feed("Sorry, something went wrong. The details are on screen.")
            console.print()
            await voice_out.finish()
            awake_until = time.monotonic() + FOLLOW_UP_SECONDS
    finally:
        await fox.stop()
