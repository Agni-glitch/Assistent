"""LangChain model providers and the fallback router."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Literal, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, message_chunk_to_message
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, ValidationError, field_validator

from fox.config import Settings
from fox.core.events import Event

log = logging.getLogger("fox.models")

PROVIDERS = ["azure", "gemini", "groq", "openai", "anthropic", "ollama"]
# "openai_compatible" covers any server that speaks the OpenAI API: OpenRouter, DeepSeek, Together,
# Mistral, LM Studio, vLLM and so on.
CUSTOM_PROVIDERS = ["openai_compatible", *PROVIDERS]
RESERVED_NAMES = {*PROVIDERS, "custom", "auto"}


class AllModelsFailed(Exception):
    pass


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str

    @property
    def label(self) -> str:
        # A model the user added is known by the name they gave it.
        return self.model if self.provider == "custom" else f"{self.provider}:{self.model}"


class CustomModel(BaseModel):
    """A model added on the Models page. Its key lives in data/models.json, which tools can't read."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,39}$")
    provider: Literal["openai_compatible", "azure", "gemini", "groq", "openai", "anthropic", "ollama"]
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=500)
    vision: bool = False  # can read images

    @field_validator("name")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        if value in RESERVED_NAMES:
            raise ValueError(f"'{value}' is a provider name. Pick another name.")
        return value

    @field_validator("model", "base_url", "api_key")
    @classmethod
    def _one_line(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("must be a single line")
        return value.strip()

    @field_validator("base_url")
    @classmethod
    def _web_address(cls, value: str) -> str:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return value

    def public(self, s: Settings) -> dict:
        """What the GUI may see: never the key itself."""
        return {**self.model_dump(exclude={"api_key"}), "has_key": bool(self.api_key), "ready": custom_ready(s, self)}


class ModelStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, CustomModel]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log.exception("Could not read %s", self.path)
            return {}
        models = {}
        for item in raw if isinstance(raw, list) else []:
            try:
                model = CustomModel.model_validate(item)
                models[model.name] = model
            except ValidationError as exc:
                log.warning("Skipping a model in %s: %s", self.path, exc)
        return models

    def _write(self, models: dict[str, CustomModel]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([m.model_dump() for m in models.values()], indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def save(self, model: CustomModel) -> None:
        models = self.load()
        models[model.name] = model
        self._write(models)

    def delete(self, name: str) -> bool:
        models = self.load()
        existed = models.pop(name, None) is not None
        self._write(models)
        return existed


def model_store(s: Settings) -> ModelStore:
    return ModelStore(s.fox_data_dir / "models.json")


def custom_models(s: Settings) -> dict[str, CustomModel]:
    return model_store(s).load()


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


def custom_ready(s: Settings, m: CustomModel) -> bool:
    """Whether a model the user added has what it needs. Without its own key, it borrows the provider's."""
    if m.provider == "openai_compatible":
        return bool(m.base_url)
    if m.provider == "ollama":
        return True
    if m.provider == "azure":
        return bool((m.base_url or s.azure_openai_endpoint) and (m.api_key or s.azure_openai_api_key))
    return bool(m.api_key) or is_configured(s, m.provider)


def parse_chain(s: Settings, chain: str | Sequence[str]) -> list[ModelSpec]:
    """Parse "azure,groq:some-model,my-model" into specs, leaving out models with no credentials.

    An entry is a provider, "provider:model", or the name of a model added on the Models page.
    """
    entries = chain.split(",") if isinstance(chain, str) else chain
    custom = custom_models(s)
    specs: list[ModelSpec] = []
    for entry in entries:
        provider, _, model = entry.strip().partition(":")
        provider = provider.lower()
        name = model.lower() if provider == "custom" else "" if model else provider
        if name in custom:
            spec = ModelSpec("custom", name) if custom_ready(s, custom[name]) else None
        else:
            model = model or _default_model(s, provider)
            spec = ModelSpec(provider, model) if provider and model and is_configured(s, provider) else None
        if spec and spec not in specs:
            specs.append(spec)
    return specs


def vision_chain(s: Settings) -> str | list[str]:
    """The models to try when a message has images."""
    if s.fox_vision_chain.strip():
        return s.fox_vision_chain
    readers = [m.name for m in custom_models(s).values() if m.vision]
    return readers + s.fox_model_chain.split(",")


def available_models(s: Settings) -> list[str]:
    """Every model that could answer right now, for the model picker."""
    builtin = [f"{p}:{_default_model(s, p)}" for p in PROVIDERS if is_configured(s, p) and _default_model(s, p)]
    return builtin + [m.name for m in custom_models(s).values() if custom_ready(s, m)]


def _construct(s: Settings, provider: str, model: str, api_key: str = "", base_url: str = "") -> BaseChatModel:
    common = {"temperature": s.fox_temperature, "timeout": s.fox_model_timeout, "max_retries": 1}
    if provider == "azure":
        from langchain_openai import AzureChatOpenAI

        return AzureChatOpenAI(
            azure_endpoint=base_url or s.azure_openai_endpoint,
            api_key=api_key or s.azure_openai_api_key,
            api_version=s.azure_openai_api_version,
            azure_deployment=model,
            **common,
        )
    if provider == "openai_compatible":
        from langchain_openai import ChatOpenAI

        # Local servers such as LM Studio need no key, but the client insists on one.
        return ChatOpenAI(model=model, api_key=api_key or "not-needed", base_url=base_url, **common)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        url = base_url or s.openai_base_url or None
        return ChatOpenAI(model=model, api_key=api_key or s.openai_api_key, base_url=url, **common)
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model, google_api_key=api_key or s.google_api_key, **common)
    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=model, api_key=api_key or s.groq_api_key, **common)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        extra = {"base_url": base_url} if base_url else {}
        return ChatAnthropic(model=model, api_key=api_key or s.anthropic_api_key, **extra, **common)
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=model, base_url=base_url or s.ollama_base_url, temperature=s.fox_temperature)
    raise ValueError(f"Unknown model provider: {provider}")


def build_model(s: Settings, spec: ModelSpec) -> BaseChatModel:
    if spec.provider == "custom":
        m = custom_models(s).get(spec.model)
        if m is None:
            raise ValueError(f"The model '{spec.model}' was removed.")
        return _construct(s, m.provider, m.model, m.api_key, m.base_url)
    return _construct(s, spec.provider, spec.model)


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
