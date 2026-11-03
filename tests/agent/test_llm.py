import json
from dataclasses import replace
from pathlib import Path

import httpx
import ollama
import openai
import pytest
from ollama._utils import convert_function_to_tool
from openai.types.chat import ChatCompletion

from app.agent.llm import LLMUnavailable, build_llm, ollama_llm, openai_llm
from app.agent.tools import StoreTools
from app.config import Settings, get_settings

SETTINGS = Settings("http://127.0.0.1:9", "any-model", 16384, 1.0, openai_model="remote-model")


def preco(produto: str) -> str:
    """Busca o preço.

    Args:
        produto: Nome do produto.
    """
    return f"{produto}: R$ 100,00"


class FakeOpenAI:
    """Stands in for openai.OpenAI: records each request and returns a scripted reply."""

    def __init__(self, *replies: dict | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.chat = self
        self.completions = self

    def create(self, **request) -> ChatCompletion:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatCompletion.model_validate(
            {
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": request["model"],
                "choices": [{"index": 0, "finish_reason": "stop", "message": reply}],
            }
        )


def openai_call(name: str, arguments: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "abc", "type": "function", "function": {"name": name, "arguments": arguments}}
        ],
    }


def test_openai_adapter_returns_a_plain_answer():
    client = FakeOpenAI({"role": "assistant", "content": " Olá! "})

    reply = openai_llm(SETTINGS, client)([{"role": "user", "content": "oi"}], [])

    assert reply == {"role": "assistant", "content": "Olá!"}
    assert client.requests == [
        {"model": "remote-model", "messages": [{"role": "user", "content": "oi"}]}
    ]


def test_openai_adapter_sends_tool_schemas_and_returns_plain_tool_calls():
    client = FakeOpenAI(openai_call("preco", '{"produto": "GD20"}'))

    reply = openai_llm(SETTINGS, client)([{"role": "user", "content": "Quanto custa?"}], [preco])

    schema = client.requests[0]["tools"][0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "preco"
    assert schema["function"]["parameters"]["properties"]["produto"]["type"] == "string"
    assert reply == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "preco", "arguments": {"produto": "GD20"}}}],
    }


def test_openai_adapter_links_each_tool_result_to_its_call():
    client = FakeOpenAI({"role": "assistant", "content": "Custam R$ 100,00."})
    both = [
        {"function": {"name": "preco", "arguments": {"produto": "A"}}},
        {"function": {"name": "preco", "arguments": {"produto": "B"}}},
    ]
    history = [
        {"role": "system", "content": "regras"},
        {"role": "user", "content": "Compare"},
        {"role": "assistant", "content": "", "tool_calls": both},
        {"role": "tool", "tool_name": "preco", "content": "A: R$ 100,00"},
        {"role": "tool", "tool_name": "preco", "content": "B: R$ 100,00"},
    ]

    openai_llm(SETTINGS, client)(history, [preco])

    sent = client.requests[0]["messages"]
    assert sent[:2] == history[:2]
    assert sent[2]["content"] is None
    ids = [call["id"] for call in sent[2]["tool_calls"]]
    assert len(set(ids)) == 2
    assert json.loads(sent[2]["tool_calls"][1]["function"]["arguments"]) == {"produto": "B"}
    assert sent[3:] == [
        {"role": "tool", "tool_call_id": ids[0], "content": "A: R$ 100,00"},
        {"role": "tool", "tool_call_id": ids[1], "content": "B: R$ 100,00"},
    ]


@pytest.mark.parametrize("arguments", ['{"produto": "GD', '["GD20"]'])
def test_openai_adapter_passes_invalid_arguments_on_for_the_model_to_fix(arguments):
    client = FakeOpenAI(openai_call("preco", arguments))

    reply = openai_llm(SETTINGS, client)([{"role": "user", "content": "oi"}], [preco])

    assert reply["tool_calls"][0]["function"]["arguments"] == {"json_invalido": arguments}


REQUEST = httpx.Request("POST", "http://x")


@pytest.mark.parametrize(
    "error",
    [
        openai.APIConnectionError(request=REQUEST),
        openai.APITimeoutError(request=REQUEST),
        openai.AuthenticationError(
            "bad key", response=httpx.Response(401, request=REQUEST), body=None
        ),
    ],
)
def test_openai_errors_become_llm_unavailable(error):
    with pytest.raises(LLMUnavailable):
        openai_llm(SETTINGS, FakeOpenAI(error))([{"role": "user", "content": "oi"}], [])


def test_openai_adapter_treats_an_empty_reply_as_unavailable():
    client = FakeOpenAI({"role": "assistant", "content": "  "})
    with pytest.raises(LLMUnavailable):
        openai_llm(SETTINGS, client)([{"role": "user", "content": "oi"}], [])


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


def test_ollama_is_the_default_provider(monkeypatch):
    for name in ("LLM_PROVIDER", "OPENAI_MODEL", "OPENAI_CONTEXT_TOKENS", "OLLAMA_NUM_CTX"):
        monkeypatch.delenv(name, raising=False)
    settings = get_settings()

    assert settings.llm_provider == "ollama"
    assert settings.context_tokens == settings.ollama_num_ctx == 16384
    assert build_llm(settings).__qualname__.startswith("ollama_llm")


def test_openai_provider_uses_its_own_settings(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "OpenAI")
    monkeypatch.setenv("OPENAI_MODEL", "remote-model")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_CONTEXT_TOKENS", "64000")
    settings = get_settings()

    assert settings.llm_provider == "openai"
    assert settings.openai_base_url == "http://localhost:11434/v1"
    assert settings.context_tokens == 64000
    assert build_llm(settings).__qualname__.startswith("openai_llm")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"llm_provider": "gemini"}, "Unknown LLM_PROVIDER 'gemini'"),
        ({"llm_provider": "openai", "openai_model": ""}, "Set OPENAI_MODEL"),
    ],
)
def test_wrong_provider_settings_fail_with_a_clear_message(changes, message):
    with pytest.raises(ValueError, match=message):
        build_llm(replace(SETTINGS, **changes))
