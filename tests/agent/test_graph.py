import re
from contextlib import closing
from datetime import datetime
from pathlib import Path

import httpx
import ollama
import pytest
from ollama._utils import convert_function_to_tool

from app.agent.graph import MAX_TOOL_ROUNDS, Agent, LLMUnavailable, ollama_llm
from app.agent.prompt import build_system_prompt
from app.agent.tools import StoreTools
from app.config import Settings
from app.store.database import connect_read_only
from app.store.policy_sections import list_sections

SETTINGS = Settings("http://127.0.0.1:9", "any-model", 16384, 1.0)


def call(name: str, **arguments) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
    }


class ScriptedLLM:
    def __init__(self, *replies: str | dict | Exception) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []
        self.tools: list = []

    def __call__(self, messages: list[dict], tools) -> dict:
        self.calls.append(messages)
        self.tools.append(tools)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"role": "assistant", "content": reply} if isinstance(reply, str) else reply


def preco(produto: str) -> str:
    return f"{produto}: R$ 100,00"


def quebrada() -> str:
    raise RuntimeError("banco fora do ar")


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
        ollama_llm(SETTINGS)([{"role": "user", "content": "oi"}], [])


def test_ollama_timeout_becomes_llm_unavailable(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr("ollama.Client.chat", timeout)
    with pytest.raises(LLMUnavailable):
        ollama_llm(SETTINGS)([{"role": "user", "content": "oi"}], [])


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


def test_prompt_lists_only_sections_of_the_manual(real_db):
    with closing(connect_read_only(real_db)) as conn:
        known = {section["number"] for section in list_sections(conn)}
    index = build_system_prompt().split("# Índice do manual da loja")[1].split("\n\nAntes")[0]
    cited = re.findall(r"\b\d+(?:\.\d+)?(?=:)", index)
    assert {"3", "4.1", "5.2", "7.3", "8", "9"} <= set(cited)
    assert set(cited) <= known


def test_prompt_leaves_case_rules_to_the_manual():
    prompt = build_system_prompt()
    assert "consultar_politica" in prompt
    assert "7 dias" not in prompt
    assert "ainda não tem acesso" not in prompt


def test_tool_call_runs_and_its_result_reaches_the_model():
    llm = ScriptedLLM(call("preco", produto="GD20"), "Custa R$ 100,00.")
    agent = Agent(llm, [preco])

    assert agent.ask("s1", "Quanto custa?") == "Custa R$ 100,00."

    assert llm.tools[0] == [preco]
    assert llm.calls[1][-2]["tool_calls"][0]["function"]["name"] == "preco"
    assert llm.calls[1][-1] == {
        "role": "tool",
        "tool_name": "preco",
        "content": "GD20: R$ 100,00",
    }


def test_model_can_call_several_tools_at_once():
    both = call("preco", produto="A")
    both["tool_calls"] += call("preco", produto="B")["tool_calls"]
    llm = ScriptedLLM(both, "pronto")

    Agent(llm, [preco]).ask("s1", "Compare")

    assert [m["content"] for m in llm.calls[1][-2:]] == ["A: R$ 100,00", "B: R$ 100,00"]


def test_tool_results_stay_in_context():
    llm = ScriptedLLM(call("preco", produto="GD20"), "R$ 100,00", "Isso, R$ 100,00.")
    agent = Agent(llm, [preco])

    agent.ask("s1", "Quanto custa?")
    agent.ask("s1", "Confirma?")

    assert [m["role"] for m in llm.calls[2]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (call("quebrada"), "banco fora do ar"),
        (call("inexistente"), "preco, quebrada"),
        (call("preco", errado=1), "errado"),
    ],
)
def test_tool_error_goes_back_to_the_model_as_text(reply, expected):
    llm = ScriptedLLM(reply, "Não consegui consultar agora.")
    agent = Agent(llm, [preco, quebrada])

    assert agent.ask("s1", "Quanto custa?") == "Não consegui consultar agora."

    result = llm.calls[1][-1]
    assert result["role"] == "tool"
    assert result["content"].startswith("Erro ao executar")
    assert expected in result["content"]


def test_round_limit_ends_the_turn_with_a_fallback():
    llm = ScriptedLLM(*[call("preco", produto="x")] * MAX_TOOL_ROUNDS, "próxima")
    agent = Agent(llm, [preco])

    reply = agent.ask("s1", "Quanto custa?")

    assert "(67) 3341-4444" in reply
    assert len(llm.calls) == MAX_TOOL_ROUNDS
    assert agent.history("s1")[-1]["content"] == reply
    # Every call in the stored turn has its result, so the next message is valid.
    assert agent.ask("s1", "E agora?") == "próxima"
    assert [m["role"] for m in llm.calls[-1][-2:]] == ["assistant", "user"]


def test_failure_after_a_tool_call_leaves_no_trace_in_the_session():
    llm = ScriptedLLM("ok", call("preco", produto="x"), LLMUnavailable("down"), "voltei")
    agent = Agent(llm, [preco])

    agent.ask("s1", "primeira")
    with pytest.raises(LLMUnavailable):
        agent.ask("s1", "perdida")
    assert agent.ask("s1", "terceira") == "voltei"

    assert [m["content"] for m in llm.calls[-1][1:]] == ["primeira", "ok", "terceira"]
    assert [m["content"] for m in agent.history("s1")] == ["primeira", "ok", "terceira", "voltei"]


def test_ollama_adapter_sends_tools_and_returns_plain_tool_calls(monkeypatch):
    sent = {}

    def chat(self, **kwargs):
        sent.update(kwargs)
        return ollama.ChatResponse(
            message=ollama.Message(
                role="assistant",
                tool_calls=[
                    ollama.Message.ToolCall(
                        function=ollama.Message.ToolCall.Function(
                            name="preco", arguments={"produto": "GD20"}
                        )
                    )
                ],
            )
        )

    monkeypatch.setattr("ollama.Client.chat", chat)
    reply = ollama_llm(SETTINGS)([{"role": "user", "content": "oi"}], [preco])

    assert sent["tools"] == [preco]
    assert sent["options"] == {"num_ctx": 16384}
    # Plain values only: checkpoints must be able to store the reply.
    assert reply == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "preco", "arguments": {"produto": "GD20"}}}],
    }


def test_ollama_adapter_treats_an_empty_reply_as_unavailable(monkeypatch):
    def chat(self, **kwargs):
        return ollama.ChatResponse(message=ollama.Message(role="assistant", content="  "))

    monkeypatch.setattr("ollama.Client.chat", chat)
    with pytest.raises(LLMUnavailable):
        ollama_llm(SETTINGS)([{"role": "user", "content": "oi"}], [])


def test_tool_schemas_are_valid_for_the_model():
    tools = StoreTools(Path("unused.db")).all()
    schemas = {
        tool.__name__: convert_function_to_tool(tool).function.parameters for tool in tools
    }

    assert set(schemas) == {
        "consultar_politica",
        "buscar_produtos",
        "consultar_promocoes",
        "consultar_pedido",
        "calcular_parcelamento",
        "registrar_encaminhamento",
    }
    for parameters in schemas.values():
        # A type such as "array, string" isn't valid JSON Schema.
        assert all("," not in prop.type for prop in parameters.properties.values())
        assert all(prop.description for prop in parameters.properties.values())
    assert schemas["buscar_produtos"].properties["termos"].type == "array"
    assert schemas["buscar_produtos"].properties["limite"].type == "integer"
    assert schemas["consultar_pedido"].properties["numero_pedido"].type == "integer"
    assert not schemas["buscar_produtos"].required
    assert schemas["calcular_parcelamento"].required == ["valor"]
    assert set(schemas["registrar_encaminhamento"].required) == {"tipo", "resumo", "contato"}
