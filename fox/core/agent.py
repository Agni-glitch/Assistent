"""The agent loop: ask the model, run the tools it picks, repeat until it answers."""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage

from fox.core.events import Emit, Event
from fox.core.models import ModelRouter, text_of
from fox.core.tooling import REGISTRY, ToolExecutor


@dataclass
class AgentResult:
    text: str
    model: str = ""
    session_id: str = ""
    new_messages: list[BaseMessage] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        router: ModelRouter,
        executor: ToolExecutor,
        tools: list[str],
        max_steps: int = 15,
        stream_text: bool = True,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.router = router
        self.executor = executor
        self.tool_names = [t for t in tools if t in REGISTRY]
        self.max_steps = max_steps
        self.stream_text = stream_text

    async def run(self, history: list[BaseMessage], emit: Emit, result: AgentResult | None = None) -> AgentResult:
        """Run a turn. Pass `result` to keep completed steps if the run is cancelled partway."""
        result = result or AgentResult(text="")
        tools = [REGISTRY[t].tool for t in self.tool_names]
        allowed = set(self.tool_names)

        async def on_model_event(event: Event) -> None:
            if self.stream_text or event.type == "model_switch":
                await emit(event)

        for _ in range(self.max_steps):
            messages = [SystemMessage(self.system_prompt), *history, *result.new_messages]
            ai: AIMessage = await self.router.stream(messages, tools, on_model_event)
            result.model = ai.response_metadata.get("fox_model", "")

            if not ai.tool_calls and not ai.invalid_tool_calls:
                result.new_messages.append(ai)
                result.text = text_of(ai.content)
                return result

            # One at a time, so permission prompts appear in a clear order.
            replies: list[BaseMessage] = []
            for call in ai.tool_calls:
                output = await self.executor.execute(call["name"], call["args"], self.name, allowed, emit)
                replies.append(ToolMessage(output, tool_call_id=call["id"], name=call["name"]))
            for bad in ai.invalid_tool_calls:
                replies.append(
                    ToolMessage(
                        f"Error: could not parse these arguments: {bad.get('error')}",
                        tool_call_id=bad["id"],
                        name=bad.get("name") or "unknown",
                    )
                )
            # Added together: an AI tool call without its results would break the next request.
            result.new_messages.extend([ai, *replies])

        result.text = f"I stopped after {self.max_steps} steps without finishing. Tell me if I should continue."
        result.new_messages.append(AIMessage(result.text))
        if self.stream_text:
            await emit(Event("text", {"delta": result.text}))
        return result
