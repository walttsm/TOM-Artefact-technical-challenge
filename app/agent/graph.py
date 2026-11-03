"""LangGraph agent: an agent <-> tools loop, with one conversation per thread_id."""

import json
import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from inspect import getdoc, signature
from operator import add
from typing import Annotated, Any, Literal, NotRequired, TypedDict

import httpx
import ollama
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Overwrite

from app.agent.prompt import build_summary_prompt, build_system_prompt
from app.agent.tools import StoreTools
from app.config import Settings
from app.policy.privacy import is_masked, pseudonymize, reveal

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
# Every request checks its own session. The scan of all sessions runs less often.
EXPIRY_SCAN_INTERVAL = timedelta(hours=1)
CONTACT_EXPIRED = (
    "os dados de contato mascarados não estão mais disponíveis. "
    "Peça ao cliente para informá-los de novo."
)
# Tokens are estimated from characters. Measured with gemma4:12b, the prompt has about
# 3.9 characters per token and a JSON tool result about 2.7, so 2.5 errs on the safe side.
CHARS_PER_TOKEN = 2.5
# Before a turn, a context estimated above COMPACT_AT of num_ctx is cut to COMPACT_TO by
# folding its oldest turns into a summary. The gap leaves room for the turn's tool results
# and means a summary is written now and then, not on every turn.
COMPACT_AT = 0.6
COMPACT_TO = 0.4
KEEP_RECENT_TURNS = 2
SUMMARY_HEADING = (
    "# Resumo do início desta conversa\n"
    "Preços, estoque e pedidos citados antes podem ter mudado: consulte de novo."
)
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
    # The model sees a summary of the first `summarized` messages, then the rest of them.
    summary: NotRequired[str]
    summarized: NotRequired[int]


def estimate_tokens(messages: Sequence[Message], tools: Sequence[Tool] = ()) -> int:
    chars = sum(len(message["content"]) for message in messages)
    chars += sum(len(json.dumps(message.get("tool_calls", ""))) for message in messages)
    chars += sum(len(tool.__name__) + len(getdoc(tool) or "") for tool in tools)
    return round(chars / CHARS_PER_TOKEN)


def _transcript(messages: Sequence[Message]) -> str:
    lines = []
    for message in messages:
        if message["role"] == "user":
            lines.append(f"Cliente: {message['content']}")
        elif message["role"] == "tool":
            lines.append(f"Resultado de {message['tool_name']}: {message['content']}")
        else:
            for call in message.get("tool_calls", []):
                arguments = json.dumps(call["function"]["arguments"], ensure_ascii=False)
                lines.append(f"Tom consultou {call['function']['name']}({arguments})")
            if message["content"]:
                lines.append(f"Tom: {message['content']}")
    return "\n".join(lines)


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


def _run_tool(tools: dict[str, Tool], call: ToolCall, vault: dict[str, str]) -> Message:
    name = call["function"]["name"]
    try:
        if name not in tools:
            raise ValueError(f"essa ferramenta não existe. Use uma destas: {', '.join(tools)}.")
        # The model only sees masks. The real values reach the tool and nothing else.
        arguments = {
            key: reveal(value, vault) for key, value in call["function"]["arguments"].items()
        }
        if any(is_masked(value) for value in arguments.values()):
            raise ValueError(CONTACT_EXPIRED)
        accepted = signature(tools[name]).parameters
        if unknown := [argument for argument in arguments if argument not in accepted]:
            raise ValueError(
                f"argumentos inválidos: {', '.join(unknown)}. "
                f"Chame de novo usando estes: {', '.join(accepted)}."
            )
        content = tools[name](**arguments)
    except Exception as exc:
        # The model reads the error and can fix its arguments or explain the problem.
        logger.warning("Tool %s failed: %s", name, exc)
        content = f"Erro ao executar {name}: {exc}"
    # In case a result repeats a contact, it goes back to the model masked too.
    return {"role": "tool", "tool_name": name, "content": pseudonymize(content, vault)}


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: Sequence[Tool] = (),
        checkpointer: BaseCheckpointSaver | None = None,
        context_tokens: int | None = None,
        session_ttl: timedelta | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._checkpointer = checkpointer or InMemorySaver()
        self._session_ttl = session_ttl
        self._clock = clock
        self._next_expiry_scan = clock()
        tools_by_name = {tool.__name__: tool for tool in tools}
        # Real contact values per session, in memory only: checkpoints keep the masks.
        self._vaults: dict[str, dict[str, str]] = {}

        def context(state: AgentState) -> list[Message]:
            prompt = build_system_prompt()
            if summary := state.get("summary"):
                prompt = f"{prompt}\n\n{SUMMARY_HEADING}\n{summary}"
            system: Message = {"role": "system", "content": prompt}
            return [system, *state["messages"][state.get("summarized", 0) :], *state["turn"]]

        def context_size(state: AgentState, summarized: int | None = None) -> int:
            if summarized is not None:
                state = {**state, "summarized": summarized}
            return estimate_tokens(context(state), tools)

        def compact(state: AgentState) -> dict[str, Any]:
            """Fold the oldest turns into the summary when the context grows too large."""
            if not context_tokens or context_size(state) <= context_tokens * COMPACT_AT:
                return {}
            messages, start = state["messages"], state.get("summarized", 0)
            turn_starts = [i for i, m in enumerate(messages) if i > start and m["role"] == "user"]
            # Folding stops before the recent turns, so the model still sees their tool results.
            ends = turn_starts[: len(turn_starts) + 1 - KEEP_RECENT_TURNS]
            if not ends:
                return {}
            target = context_tokens * COMPACT_TO
            end = next((i for i in ends if context_size(state, i) <= target), ends[-1])
            previous = state.get("summary") or "(nenhum)"
            request = (
                f"Resumo anterior:\n{previous}\n\n"
                f"Novas mensagens:\n{_transcript(messages[start:end])}"
            )
            summary = llm(
                [
                    {"role": "system", "content": build_summary_prompt()},
                    {"role": "user", "content": request},
                ],
                (),
            )
            logger.info("Folded %s messages into the summary", end - start)
            return {"summary": summary["content"], "summarized": end}

        def call_model(state: AgentState) -> dict[str, list[Message]]:
            return {"turn": [llm(context(state), tools)]}

        def call_tools(state: AgentState, config: RunnableConfig) -> dict[str, list[Message]]:
            vault = self._vaults.get(config["configurable"]["thread_id"], {})
            calls = state["turn"][-1]["tool_calls"]
            return {"turn": [_run_tool(tools_by_name, call, vault) for call in calls]}

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
        builder.add_node("compact", compact)
        builder.add_node("agent", call_model)
        builder.add_node("tools", call_tools)
        builder.add_node("give_up", give_up)
        builder.add_node("finish_turn", finish_turn)
        builder.add_edge(START, "compact")
        builder.add_edge("compact", "agent")
        builder.add_conditional_edges("agent", after_model)
        builder.add_conditional_edges("tools", after_tools)
        builder.add_edge("give_up", "finish_turn")
        builder.add_edge("finish_turn", END)
        self._graph = builder.compile(checkpointer=self._checkpointer)
        self._expire_idle_sessions()

    def ask(self, session_id: str, question: str) -> str:
        self._expire_idle_sessions(session_id)
        masked = pseudonymize(question, self._vaults.setdefault(session_id, {}))
        turn_start = Overwrite([{"role": "user", "content": masked}])
        # One checkpoint per turn, rather than one per step, keeps the expiry scan short.
        state = self._graph.invoke(
            {"messages": [], "turn": turn_start}, _thread(session_id), durability="exit"
        )
        return state["messages"][-1]["content"]

    def history(self, session_id: str) -> list[Message]:
        """What the customer and the assistant said, without the tool calls."""
        self._expire_idle_sessions(session_id)
        messages = self._graph.get_state(_thread(session_id)).values.get("messages", [])
        return [
            {"role": message["role"], "content": message["content"]}
            for message in messages
            if message["role"] in ("user", "assistant") and not message.get("tool_calls")
        ]

    def _expire_idle_sessions(self, session_id: str | None = None) -> None:
        """Delete sessions idle for longer than the TTL: this one, or all when a scan is due."""
        if self._session_ttl is None:
            return
        now = self._clock()
        last_active: dict[str, str] = {}
        if now >= self._next_expiry_scan:
            self._next_expiry_scan = now + EXPIRY_SCAN_INTERVAL
            for saved in self._checkpointer.list(None):
                thread_id = saved.config["configurable"]["thread_id"]
                ts = saved.checkpoint["ts"]
                last_active[thread_id] = max(last_active.get(thread_id, ts), ts)
        elif session_id and (saved := self._checkpointer.get_tuple(_thread(session_id))):
            last_active[session_id] = saved.checkpoint["ts"]
        for thread_id, ts in last_active.items():
            if now - datetime.fromisoformat(ts) > self._session_ttl:
                self._checkpointer.delete_thread(thread_id)
                self._vaults.pop(thread_id, None)
                logger.info("Expired idle session %s", thread_id)


@contextmanager
def store_agent(settings: Settings) -> Iterator[Agent]:
    """The real agent: Ollama, the store's tools and history that survives restarts."""
    settings.checkpoints_path.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(settings.checkpoints_path)) as checkpointer:
        yield Agent(
            ollama_llm(settings),
            StoreTools(settings.db_path).all(),
            checkpointer,
            context_tokens=settings.ollama_num_ctx,
            session_ttl=timedelta(days=settings.session_ttl_days),
        )


def _thread(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}
