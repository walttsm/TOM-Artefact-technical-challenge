"""HTTP interface. Run with: uvicorn app.api.main:app"""

from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.agent.graph import Agent, LLMUnavailable, ollama_llm
from app.config import get_settings


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(
        default=None, max_length=64, description="Omita para iniciar uma nova conversa."
    )


class ChatResponse(BaseModel):
    session_id: str
    reply: str


def create_app(agent: Agent | None = None) -> FastAPI:
    agent = agent or Agent(ollama_llm(get_settings()))
    app = FastAPI(title="Agente de perguntas e respostas")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/chat", response_model=ChatResponse)
    def chat(body: ChatRequest) -> ChatResponse:
        session_id = body.session_id or str(uuid4())
        try:
            reply = agent.ask(session_id, body.message)
        except LLMUnavailable as exc:
            raise HTTPException(status_code=502, detail=f"LLM unavailable: {exc}") from exc
        return ChatResponse(session_id=session_id, reply=reply)

    @app.get("/sessions/{session_id}/messages")
    def messages(session_id: str) -> dict[str, list[dict[str, str]]]:
        return {"messages": agent.history(session_id)}

    return app


app = create_app()
