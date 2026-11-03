"""The models the agent can run on. Each adapter is a function from messages and tools to
an assistant message, so the graph never depends on a provider."""

import json
from collections.abc import Callable, Sequence
from typing import Any, NotRequired, TypedDict

import httpx
import ollama
import openai
from ollama._utils import convert_function_to_tool

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
        calls: list[ToolCall] = [
            {"function": {"name": call.function.name, "arguments": dict(call.function.arguments)}}
            for call in response.message.tool_calls or []
        ]
        return _reply(response.message.content, calls)

    return call


def openai_llm(settings: Settings, client: openai.OpenAI | None = None) -> LLM:
    """Any server that speaks the OpenAI Chat Completions API: OpenAI or a compatible one."""
    client = client or openai.OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.openai_timeout,
    )

    def call(messages: list[Message], tools: Sequence[Tool]) -> Message:
        # The SDK leaves out an argument only when it isn't passed at all.
        options: dict[str, Any] = {"tools": _tool_schemas(tools)} if tools else {}
        try:
            response = client.chat.completions.create(
                model=settings.openai_model, messages=_to_openai(messages), **options
            )
        except openai.OpenAIError as exc:
            raise LLMUnavailable(str(exc)) from exc
        if not response.choices:
            raise LLMUnavailable("The model returned no choices.")
        message = response.choices[0].message
        calls: list[ToolCall] = [
            {"function": {"name": call.function.name, "arguments": _parse_arguments(call)}}
            for call in message.tool_calls or []
            if call.type == "function"
        ]
        return _reply(message.content, calls)

    return call


def _reply(content: str | None, calls: list[ToolCall]) -> Message:
    answer = (content or "").strip()
    if not answer and not calls:
        raise LLMUnavailable("The model returned an empty answer.")
    reply: Message = {"role": "assistant", "content": answer}
    if calls:
        reply["tool_calls"] = calls
    return reply


def _tool_schemas(tools: Sequence[Tool]) -> list[dict]:
    # Ollama's converter reads the same signatures and docstrings, and its output is
    # already in the OpenAI format, so both providers see the same schemas.
    return [convert_function_to_tool(tool).model_dump(exclude_none=True) for tool in tools]


def _to_openai(messages: Sequence[Message]) -> list[dict]:
    """Stored messages have no tool call IDs, so they are made up from each call's position.

    The graph stores each tool result right after its call, in the same order.
    """
    converted: list[dict] = []
    waiting: list[str] = []
    for position, message in enumerate(messages):
        if message["role"] == "tool":
            call_id = waiting.pop(0) if waiting else f"call_{position}"
            converted.append(
                {"role": "tool", "tool_call_id": call_id, "content": message["content"]}
            )
        elif calls := message.get("tool_calls"):
            waiting = [f"call_{position}_{index}" for index in range(len(calls))]
            converted.append(
                {
                    "role": "assistant",
                    "content": message["content"] or None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": call["function"]["name"],
                                "arguments": json.dumps(
                                    call["function"]["arguments"], ensure_ascii=False
                                ),
                            },
                        }
                        for call_id, call in zip(waiting, calls)
                    ],
                }
            )
        else:
            converted.append({"role": message["role"], "content": message["content"]})
    return converted


def _parse_arguments(call: Any) -> dict[str, Any]:
    try:
        arguments = json.loads(call.function.arguments or "{}")
    except ValueError:
        arguments = None
    # Passed on under a name no tool accepts, so the model is told to call again.
    return arguments if isinstance(arguments, dict) else {"json_invalido": call.function.arguments}
