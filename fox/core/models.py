"""LangChain model providers and the fallback router."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, message_chunk_to_message
from langchain_core.tools import BaseTool

from fox.config import Settings
from fox.core.events import Event

log = logging.getLogger("fox.models")


class AllModelsFailed(Exception):
    pass


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


def _default_model(s: Settings, provider: str) -> str:
    return {
        "azure": s.azure_openai_chat_deployment,
        "openai": s.openai_model,
        "gemini": s.gemini_model,
        "groq": s.groq_model,
        "anthropic": s.anthropic_model,
        "ollama": s.ollama_model,
    }.get(provider, "")


def is_configured(s: Settings, provider: str) -> bool:
    return {
        "azure": bool(s.azure_openai_endpoint and s.azure_openai_api_key),
        "openai": bool(s.openai_api_key),
        "gemini": bool(s.google_api_key),
        "groq": bool(s.groq_api_key),
        "anthropic": bool(s.anthropic_api_key),
        "ollama": bool(s.ollama_model),
    }.get(provider, False)


def parse_chain(s: Settings, chain: str | Sequence[str]) -> list[ModelSpec]:
    """Parse "azure,groq:some-model" into specs, leaving out providers with no credentials."""
    entries = chain.split(",") if isinstance(chain, str) else chain
    specs = []
    for entry in entries:
        provider, _, model = entry.strip().partition(":")
        provider = provider.lower()
        model = model or _default_model(s, provider)
        if provider and model and is_configured(s, provider):
            specs.append(ModelSpec(provider, model))
    return specs


def build_model(s: Settings, spec: ModelSpec) -> BaseChatModel:
    common = {"temperature": s.fox_temperature, "timeout": s.fox_model_timeout, "max_retries": 1}
    if spec.provider == "azure":
        from langchain_openai import AzureChatOpenAI

        return AzureChatOpenAI(
            azure_endpoint=s.azure_openai_endpoint,
            api_key=s.azure_openai_api_key,
            api_version=s.azure_openai_api_version,
            azure_deployment=spec.model,
            **common,
        )
    if spec.provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=spec.model, api_key=s.openai_api_key, base_url=s.openai_base_url or None, **common)
    if spec.provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=spec.model, google_api_key=s.google_api_key, **common)
    if spec.provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=spec.model, api_key=s.groq_api_key, **common)
    if spec.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=spec.model, api_key=s.anthropic_api_key, **common)
    if spec.provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=spec.model, base_url=s.ollama_base_url, temperature=s.fox_temperature)
    raise ValueError(f"Unknown model provider: {spec.provider}")


def text_of(content) -> str:
    """Providers return content as a string or a list of blocks. Get the text either way."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type", "text") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


class ModelHealth:
    """Remembers which models recently failed, so a dead provider isn't retried on every turn."""

    def __init__(self, cooldown: float = 60):
        self.cooldown = cooldown
        self._until: dict[str, float] = {}

    def available(self, label: str) -> bool:
        return time.monotonic() >= self._until.get(label, 0)

    def record_failure(self, label: str, exc: Exception) -> None:
        status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
        if status == 400:
            # About this request (for example a content filter), not the provider's health.
            return
        self._until[label] = time.monotonic() + (self.cooldown * 5 if status in (401, 403, 404) else self.cooldown)

    def record_success(self, label: str) -> None:
        self._until.pop(label, None)


class ModelRouter:
    """Tries each model in the chain until one answers, streaming as it goes."""

    def __init__(
        self,
        settings: Settings,
        chain: str | Sequence[str] | None = None,
        health: ModelHealth | None = None,
        factory: Callable[[Settings, ModelSpec], BaseChatModel] = build_model,
    ):
        self.settings = settings
        self.specs = parse_chain(settings, chain if chain else settings.fox_model_chain)
        self.health = health or ModelHealth(settings.fox_model_cooldown)
        self.factory = factory
        self._built: dict[ModelSpec, BaseChatModel] = {}

    def _model(self, spec: ModelSpec) -> BaseChatModel:
        if spec not in self._built:
            self._built[spec] = self.factory(self.settings, spec)
        return self._built[spec]

    def _order(self) -> list[ModelSpec]:
        # Models in cooldown go last rather than being dropped: better a retry than no answer.
        ready = [s for s in self.specs if self.health.available(s.label)]
        return ready + [s for s in self.specs if s not in ready]

    async def stream(
        self,
        messages: list[BaseMessage],
        tools: list[BaseTool],
        on_event: Callable[[Event], Awaitable[None]],
    ) -> AIMessage:
        if not self.specs:
            raise AllModelsFailed(
                "No model is configured. Add an API key to .env, then run 'fox doctor' to check."
            )

        errors = []
        for attempt, spec in enumerate(self._order()):
            if attempt:
                await on_event(Event("model_switch", {"model": spec.label, "reason": errors[-1]}))
            sent_text = False
            try:
                model = self._model(spec)
                runnable = model.bind_tools(tools) if tools else model
                merged = None
                async for chunk in runnable.astream(messages):
                    merged = chunk if merged is None else merged + chunk
                    delta = text_of(chunk.content)
                    if delta:
                        sent_text = True
                        await on_event(Event("text", {"delta": delta}))
                if merged is None:
                    raise RuntimeError("the model returned an empty response")
                self.health.record_success(spec.label)
                result = message_chunk_to_message(merged)
                result.response_metadata = {**result.response_metadata, "fox_model": spec.label}
                return result
            except Exception as exc:
                log.warning("Model %s failed: %s", spec.label, exc)
                self.health.record_failure(spec.label, exc)
                errors.append(f"{spec.label}: {type(exc).__name__}: {str(exc)[:300]}")
                if sent_text:
                    # The next model starts its answer over, so clear the partial text.
                    await on_event(Event("text_reset"))
        raise AllModelsFailed("All models failed:\n" + "\n".join(errors))
