# Question-answering agent

A FastAPI service that receives questions and answers them with a LangGraph agent running on a local Ollama model. Conversations are kept per session, in memory, so they are lost when the server restarts.

The project represents "TOM", a customer support agent for "EMPÓRIO DA MÚSICA". It uses tools to look up products, prices, promotions, orders, installments and store policies, and to register complaints and requests for human support. The model must support tool calling.

## Setup

Requires Python 3.12 and [Ollama](https://ollama.com). On Windows, keep the venv at a short path, because the project path is too long for some package files.

```powershell
ollama pull gemma4:12b
py -3.12 -m venv $HOME\.venvs\emporio
& $HOME\.venvs\emporio\Scripts\python -m pip install -r requirements.txt
```

## Load the data

Put the challenge CSV files and the store manual PDF in `data/`, then build the SQLite database in `storage/emporio.db`. Run this again whenever the CSVs change. The command replaces the tables in one transaction and logs any inconsistencies it finds in the data.

```powershell
& $HOME\.venvs\emporio\Scripts\python -m app.ingest
```

## Run

```powershell
& $HOME\.venvs\emporio\Scripts\python -m uvicorn app.api.main:app
```

Ask a question, then pass the returned `session_id` to continue the conversation:

```powershell
$body = @{ message = "Quanto custa o Takamine GD20?" } | ConvertTo-Json
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
| `DATA_DIR` | `data` in the project root |
| `DB_PATH` | `storage/emporio.db` in the project root |

## Test

The tests use a scripted LLM, so they don't need Ollama.

```powershell
& $HOME\.venvs\emporio\Scripts\python -m pytest
```

## About AI usage

This project has used claude code to accelerate and help in the development process. For now, consult ROADMAP.md for more information on the implementation. The prompt for the agent was created automatically by AI for now.