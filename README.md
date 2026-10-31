# Question-answering agent

A FastAPI service that receives questions and answers them with a LangGraph agent running on a local Ollama model. Conversations are kept per session, in memory, so they are lost when the server restarts.

## Setup

Requires Python 3.12 and [Ollama](https://ollama.com). On Windows, keep the venv at a short path, because the project path is too long for some package files.

```powershell
ollama pull gemma4:12b
py -3.12 -m venv $HOME\.venvs\emporio
& $HOME\.venvs\emporio\Scripts\python -m pip install -r requirements.txt
```

## Run

```powershell
& $HOME\.venvs\emporio\Scripts\python -m uvicorn app.api.main:app
```

Ask a question, then pass the returned `session_id` to continue the conversation:

```powershell
$body = @{ message = "Qual é a capital do Mato Grosso do Sul?" } | ConvertTo-Json
Invoke-RestMethod http://localhost:8000/chat -Method Post -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

Interactive docs are at http://localhost:8000/docs.

| Endpoint | Purpose |
|---|---|
| `POST /chat` | Send `message` and an optional `session_id`. Returns `session_id` and `reply`, or 502 if the model is unavailable. |
| `GET /sessions/{id}/messages` | Read a session's history. |
| `GET /health` | Check that the service is up. |

## Configuration

Set these as environment variables or in a `.env` file.

| Variable | Default |
|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `gemma4:12b` |
| `OLLAMA_NUM_CTX` | `16384` |
| `OLLAMA_TIMEOUT` (seconds) | `300` |

## Test

The tests use a scripted LLM, so they don't need Ollama.

```powershell
& $HOME\.venvs\emporio\Scripts\python -m pytest
```
