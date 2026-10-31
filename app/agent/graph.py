"""LangGraph agent: START -> agent -> END, with one conversation per thread_id."""

from collections.abc import Callable
from operator import add
from typing import Annotated, TypedDict

import httpx
import ollama
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent.prompt import build_system_prompt
from app.config import Settings

Message = dict[str, str]
LLM = Callable[[list[Message]], str]


class LLMUnavailable(Exception):
    """The model could not produce an answer."""


class AgentState(TypedDict):
    # Kept apart from the history so a failed LLM call leaves the history untouched.
    question: str
    messages: Annotated[list[Message], add]


def ollama_llm(settings: Settings) -> LLM:
    client = ollama.Client(host=settings.ollama_host, timeout=settings.ollama_timeout)

    def call(messages: list[Message]) -> str:
        try:
            response = client.chat(
                model=settings.ollama_model,
                messages=messages,
                options={"num_ctx": settings.ollama_num_ctx},
            )
        except (ollama.ResponseError, ConnectionError, httpx.HTTPError) as exc:
            raise LLMUnavailable(str(exc)) from exc
        answer = (response.message.content or "").strip()
        if not answer:
            raise LLMUnavailable("The model returned an empty answer.")
        return answer

    return call


class Agent:
    def __init__(self, llm: LLM) -> None:
        def answer(state: AgentState) -> dict[str, list[Message]]:
            question: Message = {"role": "user", "content": state["question"]}
            system: Message = {"role": "system", "content": build_system_prompt()}
            reply = llm([system, *state.get("messages", []), question])
            return {"messages": [question, {"role": "assistant", "content": reply}]}

        builder = StateGraph(AgentState)
        builder.add_node("agent", answer)
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)
        self._graph = builder.compile(checkpointer=InMemorySaver())

    def ask(self, session_id: str, question: str) -> str:
        state = self._graph.invoke({"question": question}, _thread(session_id))
        return state["messages"][-1]["content"]

    def history(self, session_id: str) -> list[Message]:
        return self._graph.get_state(_thread(session_id)).values.get("messages", [])


def _thread(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}
