"""LangGraph agent: an agent <-> tools loop, with one conversation per thread_id."""

import logging
from collections.abc import Callable, Sequence
from operator import add
from typing import Annotated, Any, Literal, NotRequired, TypedDict

import httpx
import ollama
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Overwrite

from app.agent.prompt import build_system_prompt
from app.config import Settings

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
ROUND_LIMIT_REPLY = (
    "Desculpa, não consegui concluir essa consulta por aqui. "
    "Você pode tentar de novo com mais detalhes ou falar com a equipe "
    "pelo WhatsApp (67) 3341-4444."
)


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


class AgentState(TypedDict):
    # Finished turns only, so a failed LLM call leaves the history untouched.
    messages: Annotated[list[Message], add]
    # The turn in progress. It reaches `messages` only when the turn ends, and a
    # new turn overwrites what a failed one left here.
    turn: Annotated[list[Message], add]


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


def _run_tool(tools: dict[str, Tool], call: ToolCall) -> Message:
    name = call["function"]["name"]
    try:
        if name not in tools:
            raise ValueError(f"essa ferramenta não existe. Use uma destas: {', '.join(tools)}.")
        content = tools[name](**call["function"]["arguments"])
    except Exception as exc:
        # The model reads the error and can fix its arguments or explain the problem.
        logger.warning("Tool %s failed: %s", name, exc)
        content = f"Erro ao executar {name}: {exc}"
    return {"role": "tool", "tool_name": name, "content": content}


class Agent:
    def __init__(self, llm: LLM, tools: Sequence[Tool] = ()) -> None:
        tools_by_name = {tool.__name__: tool for tool in tools}

        def call_model(state: AgentState) -> dict[str, list[Message]]:
            system: Message = {"role": "system", "content": build_system_prompt()}
            return {"turn": [llm([system, *state["messages"], *state["turn"]], tools)]}

        def call_tools(state: AgentState) -> dict[str, list[Message]]:
            calls = state["turn"][-1]["tool_calls"]
            return {"turn": [_run_tool(tools_by_name, call) for call in calls]}

        def give_up(state: AgentState) -> dict[str, list[Message]]:
            logger.warning("Tool round limit reached (%s)", MAX_TOOL_ROUNDS)
            return {"turn": [{"role": "assistant", "content": ROUND_LIMIT_REPLY}]}

        def finish_turn(state: AgentState) -> dict[str, Any]:
            return {"messages": state["turn"], "turn": Overwrite([])}

        def after_model(state: AgentState) -> Literal["tools", "finish_turn"]:
            return "tools" if state["turn"][-1].get("tool_calls") else "finish_turn"

        def after_tools(state: AgentState) -> Literal["agent", "give_up"]:
            rounds = sum(1 for message in state["turn"] if message.get("tool_calls"))
            return "give_up" if rounds >= MAX_TOOL_ROUNDS else "agent"

        builder = StateGraph(AgentState)
        builder.add_node("agent", call_model)
        builder.add_node("tools", call_tools)
        builder.add_node("give_up", give_up)
        builder.add_node("finish_turn", finish_turn)
        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", after_model)
        builder.add_conditional_edges("tools", after_tools)
        builder.add_edge("give_up", "finish_turn")
        builder.add_edge("finish_turn", END)
        self._graph = builder.compile(checkpointer=InMemorySaver())

    def ask(self, session_id: str, question: str) -> str:
        turn_start = Overwrite([{"role": "user", "content": question}])
        state = self._graph.invoke({"messages": [], "turn": turn_start}, _thread(session_id))
        return state["messages"][-1]["content"]

    def history(self, session_id: str) -> list[Message]:
        return self._graph.get_state(_thread(session_id)).values.get("messages", [])


def _thread(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}
