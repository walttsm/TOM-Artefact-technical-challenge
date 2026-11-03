"""The models the agent can run on. Each adapter is a function from messages and tools to
an assistant message, so the graph never depends on a provider."""

from collections.abc import Callable, Sequence
from typing import Any, NotRequired, TypedDict

import httpx
import ollama

from app.config import Settings


class ToolCallFunction(TypedDict):
    name: str
    arguments: dict[str, Any]


class ToolCall(TypedDict):
    function: ToolCallFunction


class Message(TypedDict):
    """Ollama's chat format, in plain values so checkpoints can store it."""

    role: str
    content: str
    tool_calls: NotRequired[list[ToolCall]]
    tool_name: NotRequired[str]


Tool = Callable[..., str]
# Returns an assistant message, which holds an answer, tool calls or both.
LLM = Callable[[list[Message], Sequence[Tool]], Message]


class LLMUnavailable(Exception):
    """The model could not produce an answer."""


def ollama_llm(settings: Settings) -> LLM:
    client = ollama.Client(host=settings.ollama_host, timeout=settings.ollama_timeout)

    def call(messages: list[Message], tools: Sequence[Tool]) -> Message:
        try:
            response = client.chat(
                model=settings.ollama_model,
                messages=messages,
                tools=list(tools) or None,
                options={"num_ctx": settings.ollama_num_ctx},
            )
        except (ollama.ResponseError, ConnectionError, httpx.HTTPError) as exc:
            raise LLMUnavailable(str(exc)) from exc
        answer = (response.message.content or "").strip()
        calls: list[ToolCall] = [
            {"function": {"name": call.function.name, "arguments": dict(call.function.arguments)}}
            for call in response.message.tool_calls or []
        ]
        if not answer and not calls:
            raise LLMUnavailable("The model returned an empty answer.")
        reply: Message = {"role": "assistant", "content": answer}
        if calls:
            reply["tool_calls"] = calls
        return reply

    return call
