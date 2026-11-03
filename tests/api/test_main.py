import pytest
from fastapi.testclient import TestClient

from app.agent.graph import Agent, LLMUnavailable
from app.api.main import create_app


def client_with(answer) -> TestClient:
    def llm(messages, tools):
        return {"role": "assistant", "content": answer(messages)}

    return TestClient(create_app(Agent(llm)))


def test_health():
    assert client_with(lambda m: "x").get("/health").json() == {"status": "ok"}


def test_chat_creates_session_and_keeps_history():
    client = client_with(lambda messages: f"resposta {len(messages)}")

    first = client.post("/chat", json={"message": "oi"}).json()
    session_id = first["session_id"]
    second = client.post("/chat", json={"message": "e aí", "session_id": session_id}).json()

    assert first["reply"] == "resposta 2"
    assert second["reply"] == "resposta 4"
    history = client.get(f"/sessions/{session_id}/messages").json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


def test_llm_failure_returns_502():
    def failing(messages):
        raise LLMUnavailable("connection refused")

    response = client_with(failing).post("/chat", json={"message": "oi"})

    assert response.status_code == 502
    assert "connection refused" in response.json()["detail"]


@pytest.mark.parametrize("message", ["", "x" * 2001])
def test_invalid_message_length_returns_422(message):
    response = client_with(lambda m: "x").post("/chat", json={"message": message})
    assert response.status_code == 422


def test_history_shows_masks_only():
    client = client_with(lambda messages: "ok")

    session_id = client.post(
        "/chat", json={"message": "Meu e-mail é ana@jmail.com e o fone (67) 99812-3456"}
    ).json()["session_id"]

    history = client.get(f"/sessions/{session_id}/messages").json()["messages"]
    assert history[0]["content"] == "Meu e-mail é a***@jmail.com e o fone (67) *****-3456"
