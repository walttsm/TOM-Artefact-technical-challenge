"""HTTP interface. Run with: uvicorn app.api.main:app"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.agent.graph import Agent, store_agent
from app.agent.llm import LLMUnavailable
from app.config import get_settings


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(
        default=None, max_length=64, description="Omita para iniciar uma nova conversa."
    )


class ChatResponse(BaseModel):
    session_id: str
    reply: str


@asynccontextmanager
async def _serve_store_agent(app: FastAPI) -> AsyncIterator[None]:
    with store_agent(get_settings()) as agent:
        app.state.agent = agent
        yield


def create_app(agent: Agent | None = None) -> FastAPI:
    app = FastAPI(
        title="Agente de perguntas e respostas",
        lifespan=None if agent else _serve_store_agent,
    )
    app.state.agent = agent

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/chat", response_model=ChatResponse)
    def chat(body: ChatRequest) -> ChatResponse:
        session_id = body.session_id or str(uuid4())
        try:
            reply = app.state.agent.ask(session_id, body.message)
        except LLMUnavailable as exc:
            raise HTTPException(status_code=502, detail=f"LLM unavailable: {exc}") from exc
        return ChatResponse(session_id=session_id, reply=reply)

    @app.get("/sessions/{session_id}/messages")
    def messages(session_id: str) -> dict[str, list[dict[str, str]]]:
        return {"messages": app.state.agent.history(session_id)}

    return app


app = create_app()
