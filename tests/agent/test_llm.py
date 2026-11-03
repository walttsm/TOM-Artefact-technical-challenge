from pathlib import Path

import httpx
import ollama
import pytest
from ollama._utils import convert_function_to_tool

from app.agent.llm import LLMUnavailable, ollama_llm
from app.agent.tools import StoreTools
from app.config import Settings

SETTINGS = Settings("http://127.0.0.1:9", "any-model", 16384, 1.0)


def preco(produto: str) -> str:
    return f"{produto}: R$ 100,00"


def test_ollama_connection_failure_becomes_llm_unavailable():
    with pytest.raises(LLMUnavailable):
        ollama_llm(SETTINGS)([{"role": "user", "content": "oi"}], [])


def test_ollama_timeout_becomes_llm_unavailable(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr("ollama.Client.chat", timeout)
    with pytest.raises(LLMUnavailable):
        ollama_llm(SETTINGS)([{"role": "user", "content": "oi"}], [])


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
