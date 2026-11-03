"""Voice for the GUI: the same listening, speaking and spoken permissions as `fox voice`, sent as events."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time

from rich.console import Console

from fox.core.events import Emit, Event
from fox.core.permissions import Decision, PermissionRequest
from fox.runtime import Fox
from fox.voice.audio import record_utterance, stop_playback
from fox.voice.loop import EXIT_PHRASES, FOLLOW_UP_SECONDS, SentenceSpeaker, VoiceApprover, strip_wake_word
from fox.voice.stt import Transcriber
from fox.voice.tts import Speaker

log = logging.getLogger("fox.voice")


class VoiceService:
    def __init__(self, fox: Fox):
        self.fox = fox
        self.speaker = Speaker(fox.settings)
        self.transcriber = Transcriber(fox.settings)
        self.approver = VoiceApprover(self.speaker, self.transcriber, Console(quiet=True))
        self.halt = threading.Event()

    async def ask(self, req: PermissionRequest) -> Decision | None:
        """Ask for permission out loud. A click on screen cancels this, which must free the microphone."""
        stop = threading.Event()
        try:
            return await self.approver.ask(req, stop)
        except asyncio.CancelledError:
            stop.set()
            stop_playback()
            raise

    async def reply(self, text: str, session_id: str | None, emit: Emit) -> str | None:
        """Run one turn and speak the answer as it streams. Returns the session id."""
        voice_out = SentenceSpeaker(self.speaker)
        current = session_id

        async def tee(event: Event) -> None:
            nonlocal current
            if event.type == "session":
                current = event.data["session_id"]
            elif event.type == "text":
                voice_out.feed(event.data["delta"])
            elif event.type == "text_reset":
                voice_out.reset()
            await emit(event)

        try:
            await self.fox.chat(text, session_id, tee, voice=True)
        except asyncio.CancelledError:
            voice_out.reset()
            stop_playback()
            raise
        finally:
            await emit(Event("voice_state", {"state": "speaking"}))
            await voice_out.finish()
        return current

    async def conversation(self, wake: bool, session_id: str | None, emit: Emit) -> None:
        """Listen once, or keep listening for the wake word until cancelled."""
        wake_word = self.fox.settings.fox_wake_word
        awake_until = 0.0
        self.halt = halt = threading.Event()

        async def state(name: str) -> None:
            await emit(Event("voice_state", {"state": name}))

        try:
            while True:
                await state("listening")
                audio = await asyncio.to_thread(record_utterance, stop=halt)
                heard = ""
                if audio is not None:
                    await state("transcribing")
                    heard = await asyncio.to_thread(self.transcriber.transcribe, audio)
                if not heard:
                    if wake:
                        continue
                    await emit(Event("notice", {"message": "I didn't hear anything."}))
                    return

                if wake and time.monotonic() > awake_until:
                    heard = strip_wake_word(heard, wake_word)
                    if heard is None:
                        continue
                    if not heard:
                        await asyncio.to_thread(self.speaker.say, "Yes?")
                        awake_until = time.monotonic() + FOLLOW_UP_SECONDS
                        continue

                if re.sub(r"[^a-z ]", "", heard.lower()).strip() in EXIT_PHRASES:
                    await asyncio.to_thread(self.speaker.say, "Goodbye.")
                    return

                await emit(Event("voice_heard", {"text": heard}))
                await state("thinking")
                try:
                    session_id = await self.reply(heard, session_id, emit)
                except Exception as exc:
                    log.exception("Voice turn failed")
                    await emit(Event("error", {"message": str(exc)}))
                    await asyncio.to_thread(self.speaker.say, "Sorry, something went wrong. The details are on screen.")
                awake_until = time.monotonic() + FOLLOW_UP_SECONDS
                if not wake:
                    return
        except asyncio.CancelledError:
            halt.set()
            stop_playback()
            raise
        finally:
            await state("idle")
