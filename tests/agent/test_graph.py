import re
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from app.agent.graph import (
    COMPACT_AT,
    MAX_TOOL_ROUNDS,
    SUMMARY_HEADING,
    Agent,
    estimate_tokens,
    store_agent,
)
from app.agent.llm import LLMUnavailable
from app.agent.prompt import build_summary_prompt, build_system_prompt
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


def test_tool_results_stay_in_context_but_not_in_history():
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
    assert agent.history("s1") == [
        {"role": "user", "content": "Quanto custa?"},
        {"role": "assistant", "content": "R$ 100,00"},
        {"role": "user", "content": "Confirma?"},
        {"role": "assistant", "content": "Isso, R$ 100,00."},
    ]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (call("quebrada"), "banco fora do ar"),
        (call("inexistente"), "preco, quebrada"),
        (call("preco", errado=1), "errado. Chame de novo usando estes: produto."),
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


def test_history_survives_a_restart_and_a_failed_call(tmp_path):
    path = str(tmp_path / "checkpoints.db")
    with SqliteSaver.from_conn_string(path) as saver:
        llm = ScriptedLLM(
            call("preco", produto="x"),
            "R$ 100,00",
            call("preco", produto="y"),
            LLMUnavailable("down"),
        )
        agent = Agent(llm, [preco], saver)
        agent.ask("s1", "Quanto custa?")
        with pytest.raises(LLMUnavailable):
            agent.ask("s1", "perdida")

    with SqliteSaver.from_conn_string(path) as saver:
        llm = ScriptedLLM("Lembro, sim.")
        agent = Agent(llm, [preco], saver)
        assert agent.ask("s1", "Lembra?") == "Lembro, sim."

        assert [m["role"] for m in llm.calls[0]] == [
            "system",
            "user",
            "assistant",
            "tool",
            "assistant",
            "user",
        ]
        assert [m["content"] for m in agent.history("s1")] == [
            "Quanto custa?",
            "R$ 100,00",
            "Lembra?",
            "Lembro, sim.",
        ]


def test_store_agent_creates_the_checkpoint_folder(tmp_path):
    path = tmp_path / "nova" / "checkpoints.db"
    db_path = tmp_path / "emporio.db"
    db_path.touch()
    settings = Settings(
        SETTINGS.ollama_host, "any-model", 16384, 1.0, db_path=db_path, checkpoints_path=path
    )

    with store_agent(settings) as agent:
        assert agent.history("nope") == []
    assert path.exists()


def test_store_agent_needs_the_loaded_data(tmp_path):
    settings = Settings(
        SETTINGS.ollama_host, "any-model", 16384, 1.0, db_path=tmp_path / "missing.db"
    )

    with pytest.raises(RuntimeError, match="python -m app.ingest"):
        with store_agent(settings):
            pass


received: list[str] = []


def contato(email: str = "", telefone: str = "") -> str:
    received.append(f"{email} {telefone}".strip())
    return f"recebido {email} {telefone}".strip()


def test_model_sees_masks_and_tools_get_the_real_contacts(tmp_path):
    received.clear()
    path = str(tmp_path / "checkpoints.db")
    with SqliteSaver.from_conn_string(path) as saver:
        llm = ScriptedLLM(
            call("contato", email="a***@coldmail.com", telefone="(67) *****-3456"),
            "Pronto.",
            call("contato", email="a***@coldmail.com"),
            LLMUnavailable("down"),
        )
        agent = Agent(llm, [contato], saver)
        agent.ask(
            "s1", "Sou anacarol.ferreira@coldmail.com, fone (67) 99812-3456, CPF 529.982.247-25"
        )
        with pytest.raises(LLMUnavailable):
            agent.ask("s1", "De novo: anacarol.ferreira@coldmail.com")

        assert llm.calls[0][-1]["content"] == (
            "Sou a***@coldmail.com, fone (67) *****-3456, CPF [CPF omitido]"
        )
        assert received[0] == "anacarol.ferreira@coldmail.com 67998123456"
        assert llm.calls[1][-1]["content"] == "recebido a***@coldmail.com (67) *****-3456"
        assert agent.history("s1")[0]["content"].startswith("Sou a***@coldmail.com")

    stored = Path(path).read_bytes()
    for real in (b"anacarol", b"99812", b"98123456", b"529.982"):
        assert real not in stored


def test_masks_without_real_values_ask_the_model_to_request_them_again():
    llm = ScriptedLLM(call("contato", email="a***@coldmail.com"), "Pode me passar de novo?")
    agent = Agent(llm, [contato])

    agent.ask("s1", "Meu e-mail é o mesmo de antes")

    result = llm.calls[1][-1]["content"]
    assert result.startswith("Erro ao executar contato")
    assert "informá-los de novo" in result


def test_masked_email_finds_the_customers_order(real_db):
    llm = ScriptedLLM(call("consultar_pedido", numero_pedido=8, email="a***@coldmail.com"), "ok")
    agent = Agent(llm, StoreTools(real_db).all())

    agent.ask("s1", "Status do pedido 8? Meu e-mail é anacarol.ferreira@coldmail.com")

    assert '"numero": 8' in llm.calls[1][-1]["content"]


class Clock:
    """Real time plus an offset, since checkpoints are stamped with the real time."""

    def __init__(self) -> None:
        self.offset = timedelta()

    def __call__(self) -> datetime:
        return datetime.now(UTC) + self.offset


TTL = timedelta(days=30)


def test_idle_session_expires_and_starts_over():
    clock = Clock()
    llm = ScriptedLLM("Olá, Ana!", "Prazer!")
    agent = Agent(llm, session_ttl=TTL, clock=clock)
    agent.ask("s1", "Meu e-mail é ana@jmail.com")

    clock.offset = TTL + timedelta(hours=1)

    assert agent.history("s1") == []
    agent.ask("s1", "Oi de novo")
    assert [m["content"] for m in llm.calls[-1][1:]] == ["Oi de novo"]
    assert agent._vaults["s1"] == {}


def test_active_sessions_are_kept():
    clock = Clock()
    agent = Agent(ScriptedLLM("a", "b"), session_ttl=TTL, clock=clock)
    agent.ask("s1", "pergunta 1")
    agent.ask("s2", "pergunta 2")

    clock.offset = TTL - timedelta(hours=1)

    assert len(agent.history("s1")) == 2
    assert len(agent.history("s2")) == 2


@pytest.mark.parametrize("sqlite", [False, True])
def test_startup_deletes_every_idle_session(tmp_path, sqlite):
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.db")) as sqlite_saver:
        saver, clock = sqlite_saver if sqlite else InMemorySaver(), Clock()
        agent = Agent(ScriptedLLM("a", "b"), checkpointer=saver, session_ttl=TTL, clock=clock)
        agent.ask("s1", "pergunta 1")
        agent.ask("s2", "pergunta 2")

        clock.offset = TTL + timedelta(hours=1)
        Agent(ScriptedLLM(), checkpointer=saver, session_ttl=TTL, clock=clock)

        assert list(saver.list(None)) == []


def test_sessions_never_expire_without_a_ttl():
    clock = Clock()
    agent = Agent(ScriptedLLM("a"), clock=clock)
    agent.ask("s1", "pergunta")

    clock.offset = timedelta(days=365)

    assert len(agent.history("s1")) == 2


def test_session_is_checked_between_scans():
    clock = Clock()
    agent = Agent(ScriptedLLM("a"), session_ttl=timedelta(minutes=1), clock=clock)
    agent.ask("s1", "pergunta")

    # Well before the next scan of all sessions.
    clock.offset = timedelta(minutes=2)

    assert agent.history("s1") == []


# Each question is about 1000 tokens. The budget is passed when a fourth one arrives.
QUESTIONS = [letter * 2500 for letter in "abcde"]


def long_conversation_budget() -> int:
    system = estimate_tokens([{"role": "system", "content": build_system_prompt()}])
    return round((system + 3500) / COMPACT_AT)


def test_short_conversations_make_no_extra_call():
    llm = ScriptedLLM("a", "b", "c")
    agent = Agent(llm, context_tokens=16384)

    for question in ("oi", "tudo bem?", "tchau"):
        agent.ask("s1", question)

    assert len(llm.calls) == 3


def test_summary_replaces_the_oldest_turns_and_is_then_updated():
    llm = ScriptedLLM("r1", "r2", "r3", "resumo 1", "r4", "resumo 2", "r5")
    agent = Agent(llm, context_tokens=long_conversation_budget())

    for question in QUESTIONS[:4]:
        agent.ask("s1", question)

    summary_call = llm.calls[3]
    assert summary_call[0]["content"] == build_summary_prompt()
    request = summary_call[1]["content"]
    assert "Resumo anterior:\n(nenhum)" in request
    assert f"Cliente: {QUESTIONS[0]}\nTom: r1" in request
    assert QUESTIONS[1] not in request

    after = llm.calls[4]
    assert after[0]["content"].endswith(f"{SUMMARY_HEADING}\nresumo 1")
    assert [m["content"] for m in after[1:]] == [
        QUESTIONS[1],
        "r2",
        QUESTIONS[2],
        "r3",
        QUESTIONS[3],
    ]

    agent.ask("s1", QUESTIONS[4])

    request = llm.calls[5][1]["content"]
    assert "Resumo anterior:\nresumo 1" in request
    assert f"Cliente: {QUESTIONS[1]}\nTom: r2" in request
    assert llm.calls[6][0]["content"].endswith("resumo 2")
    assert llm.calls[6][1]["content"] == QUESTIONS[2]
    # Only the model's context shrinks: the history keeps every message.
    assert len(agent.history("s1")) == 10


def test_failed_summary_leaves_the_session_as_it_was():
    llm = ScriptedLLM("r1", "r2", "r3", LLMUnavailable("down"), "resumo", "r4")
    agent = Agent(llm, context_tokens=long_conversation_budget())
    for question in QUESTIONS[:3]:
        agent.ask("s1", question)

    with pytest.raises(LLMUnavailable):
        agent.ask("s1", QUESTIONS[3])
    assert len(agent.history("s1")) == 6

    assert agent.ask("s1", QUESTIONS[3]) == "r4"
    assert llm.calls[-1][0]["content"].endswith("resumo")


def test_summary_input_shows_tool_calls_and_results():
    llm = ScriptedLLM(call("preco", produto="GD20"), "r1", "r2", "r3", "resumo", "r4")
    agent = Agent(llm, [preco], context_tokens=long_conversation_budget())

    for question in QUESTIONS[:4]:
        agent.ask("s1", question)

    request = llm.calls[4][1]["content"]
    assert 'Tom consultou preco({"produto": "GD20"})' in request
    assert "Resultado de preco: GD20: R$ 100,00" in request
