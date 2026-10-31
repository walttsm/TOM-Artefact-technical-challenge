from datetime import datetime

import httpx
import pytest

from app.agent.graph import Agent, LLMUnavailable, ollama_llm
from app.agent.prompt import build_system_prompt
from app.config import Settings

SETTINGS = Settings("http://127.0.0.1:9", "any-model", 16384, 1.0)


class ScriptedLLM:
    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    def __call__(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_followup_sees_previous_turns():
    llm = ScriptedLLM("Olá, Ana!", "Você se chama Ana.")
    agent = Agent(llm)

    assert agent.ask("s1", "Meu nome é Ana") == "Olá, Ana!"
    assert agent.ask("s1", "Qual é o meu nome?") == "Você se chama Ana."

    assert llm.calls[1][0]["role"] == "system"
    assert [m["content"] for m in llm.calls[1][1:]] == [
        "Meu nome é Ana",
        "Olá, Ana!",
        "Qual é o meu nome?",
    ]


def test_sessions_do_not_mix():
    llm = ScriptedLLM("a", "b")
    agent = Agent(llm)

    agent.ask("s1", "pergunta 1")
    agent.ask("s2", "pergunta 2")

    assert [m["role"] for m in llm.calls[1]] == ["system", "user"]
    assert agent.history("s2") == [
        {"role": "user", "content": "pergunta 2"},
        {"role": "assistant", "content": "b"},
    ]


def test_failed_call_does_not_break_the_session():
    llm = ScriptedLLM("ok", LLMUnavailable("down"), "voltei")
    agent = Agent(llm)

    agent.ask("s1", "primeira")
    with pytest.raises(LLMUnavailable):
        agent.ask("s1", "perdida")
    assert agent.ask("s1", "terceira") == "voltei"

    assert [m["content"] for m in agent.history("s1")] == [
        "primeira",
        "ok",
        "terceira",
        "voltei",
    ]


def test_unknown_session_has_empty_history():
    assert Agent(ScriptedLLM()).history("nope") == []


def test_ollama_connection_failure_becomes_llm_unavailable():
    with pytest.raises(LLMUnavailable):
        ollama_llm(SETTINGS)([{"role": "user", "content": "oi"}])


def test_ollama_timeout_becomes_llm_unavailable(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr("ollama.Client.chat", timeout)
    with pytest.raises(LLMUnavailable):
        ollama_llm(SETTINGS)([{"role": "user", "content": "oi"}])


def test_prompt_includes_current_date():
    prompt = build_system_prompt(datetime(2026, 10, 31, 16, 30))
    assert "português" in prompt
    assert "sábado, 31/10/2026 16:30" in prompt
    assert "domingo, 01/11/2026" in build_system_prompt(datetime(2026, 11, 1, 9, 0))


def test_prompt_has_store_rules():
    prompt = build_system_prompt()
    assert "Empório da Música" in prompt
    assert "exclusivamente instrumentos musicais" in prompt
    assert "palhetas" in prompt


def test_prompt_resolves_manual_contradictions():
    prompt = build_system_prompt()
    assert "(67) 3341-4444" in prompt
    assert "contato@emporiodamusica.com.br" in prompt
    assert "3321-4500" not in prompt
    assert "empóriodamusica" not in prompt
    assert "parcela mínima de R$ 50,00" in prompt
