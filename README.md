# Empório da Música support agent

Tom is a text-message support agent for Empório da Música, a fictional musical instrument store in Campo Grande - MS. It answers in Brazilian Portuguese, looks up products, prices, promotions and orders, applies the store's policies, and registers complaints and requests for human support.

It runs as a FastAPI service with a LangGraph agent on a local Ollama model. Sample conversations are in [examples/](examples/).

## Setup

Requires Python 3.12 and [Ollama](https://ollama.com). The model must support tool calling. On Windows, keep the venv at a short path, because the project path is too long for some package files.

```powershell
ollama pull gemma4:12b
py -3.12 -m venv $HOME\.venvs\emporio
& $HOME\.venvs\emporio\Scripts\python -m pip install -r requirements.txt
```

## Load the data

`data/` is not in this repository. Copy the challenge's six CSV files and `políticas_da_loja.pdf` into `data/`, then build the database:

```powershell
& $HOME\.venvs\emporio\Scripts\python -m app.ingest
```

This creates `storage/emporio.db` and logs the inconsistencies it finds. Run it again whenever the files change. It replaces the tables in one transaction and keeps registered handoffs.

## Run

```powershell
& $HOME\.venvs\emporio\Scripts\python -m uvicorn app.api.main:app
```

Send a message, then pass the returned `session_id` to continue the conversation:

```powershell
$body = @{ message = "Quanto custa o Takamine GD20?" } | ConvertTo-Json
Invoke-RestMethod http://localhost:8000/chat -Method Post -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

Interactive docs are at http://localhost:8000/docs, and `requests.http` has ready-made requests.

| Endpoint | Purpose |
|---|---|
| `POST /chat` | Send `message` and an optional `session_id`. Returns `session_id` and `reply`, or 502 if the model is unavailable. |
| `GET /sessions/{id}/messages` | Read a session's history, without tool calls. |
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
| `CHECKPOINTS_PATH` | `storage/checkpoints.db` in the project root |

## Test

The tests use a scripted LLM, so they don't need Ollama. Tests that read the real files are skipped if `data/` is missing.

```powershell
& $HOME\.venvs\emporio\Scripts\python -m pytest
```

To regenerate the sample conversations with the real model:

```powershell
& $HOME\.venvs\emporio\Scripts\python -m scripts.generate_examples
```

The script works on a temporary copy of the database, so it leaves no handoffs behind.

## Technical decisions

`decisions.md` records every decision in detail. These are the main ones.

### Architecture

```
FastAPI -> LangGraph agent -> tools -> data store (SQLite): what the facts are
                                    -> policy (pure Python): what the rules allow
```

Facts and rules are separate layers that never import each other, and only the tools combine them. Rules such as "a promotion cancels the PIX discount" are plain functions, so they can be tested without a database or a model.

### Function calling with fixed tools, not free-form SQL

The questions the store receives are known, and a wrong answer about a price or an order is expensive. Fixed tools give the model no way to read another customer's data or to do its own price arithmetic. Identity checks and prices run in code, so a prompt injection can't talk its way past them.

### Section lookup, not embeddings

The manual is short and already numbered, so the prompt holds an index and `consultar_politica` returns a whole section. This needs no embedding model or vector store, and the model always sees a complete rule rather than a fragment. The cost is that the model has to choose the right section.

### LangGraph

The agent is a small explicit graph: `agent <-> tools`, with a round limit. LangGraph supplies the checkpoints and per-session threads. The turn in progress is kept apart from the finished history, so a model failure partway through a turn leaves the session intact.

### Ollama and `gemma4:12b`

A local model has no API cost or key, so anyone can run the project, and customer data stays on the machine. The trade-off is slower and less reliable answers, which is why guarantees live in code wherever possible. `gemma4:12b` handled the test scenarios at about 3 to 20 seconds per message on the development machine. A systematic comparison with other models is still to be done.

### Prompt strategy

The prompt holds what applies to every message: persona, service flow, store details, when to use each tool, what must never be invented, scope and privacy. Case-specific rules come from the manual on demand. The prompt was revised after reading real conversations, and `decisions.md` and `investigations.md` record the failures behind those changes.

### History in SQLite

Support conversations continue over time, so sessions survive a server restart through LangGraph's `SqliteSaver`. Checkpoints sit in a separate file from the store's data.

## Assumptions

The brief asks for reasonable interpretations where the material is ambiguous.

### Contradictions in the manual

| Contradiction | Decision |
|---|---|
| WhatsApp (67) 3341-4444 vs (67) 3321-4500 | 3341-4444, on the cover and in section 1.2 |
| `contato@emporiodamusica` vs `contato@empóriodamusica` | No accent |
| R$ 100 minimum installment vs the tiers in section 3.1 | Tiers in 3.1 |
| "No minimum (except below R$ 50)" for up to 3x | R$ 50 minimum, and one payment below that is allowed |
| "Mais de 300 instrumentos" | Removed: the catalog has 65 products |
| "Sugerir lojas parceiras" | Removed: no list of partner stores exists |

### Inconsistencies in the data

The source values are kept, because nothing shows which conflicting value is right. The quality report logs the inconsistencies at ingestion.

| Seen | Treatment |
|---|---|
| Orders 3 and 20: total differs from the items | The recorded total stands, and no item prices are shown |
| Products 130 and 136: unavailable, with stock | Status wins over stock |
| Categories 6, 7 and 8: no products | Kept, and searches return nothing |
| Customers 11 and 43 share an email | That email alone doesn't open an order: the phone is required |
| Orders 3 and 14 contain an unreleased product | Reported as recorded, with no promised date |
| Orders 9, 16 and 18: below the installment minimum | Kept: current rules apply only to new quotes |
| Repeated and off-range promotions, all inactive | Expired promotions appear once, with no percentage |

### Other assumptions

- `is_active` alone decides whether a promotion is on, over the manual's campaign calendar.
- The persona's name, Tom, is not in the manual.
- With an order number, one matching contact is enough to view an order.

## Known limitations

- **Model reliability:** answers vary between runs, and each scenario was run only a few times. `investigations.md` lists what was seen.
- **Follow-up questions:** the model can reuse earlier tool results, so prices in a long conversation can be out of date.
- **Security:** the API has no authentication or rate limiting, so anyone with a `session_id` can read that conversation. Identity checks are basic and suit a prototype only.
- **Personal data:** history is stored in plain text, with no expiry.
- **Context size:** history has no size limit, so a very long session can exceed `num_ctx`.
- **Handoffs:** they are saved but notify no one. If the model fails right after registering one, a retry can register it twice.
- **Search:** it is keyword-based, doesn't handle typos, and matches words in descriptions, so a search can include a related product. A term combining a campaign and a product returns nothing.
- **Missing features:** there is no shipping calculation by postal code, holiday calendar, purchase channel or real delivery date.
- **Overdue orders:** the agent reports recorded dates and doesn't flag an estimate that has already passed.

## With more time

- Compare models systematically, with an automated evaluation of the scenarios over repeated runs.
- Try external LLM APIs, which could improve answer quality and speed at the cost of API fees and of sending customer data to a third party.
- Add authentication, rate limiting, and expiry and trimming for history.
- Make handoffs idempotent and notify the team.
- Add fuzzy product search and a shipping quote.
- Add streaming replies and a simple chat interface.
- Package the API with Docker.

## Use of coding assistants

I used Claude Code throughout, for planning, implementing and reviewing. I ran it inside Visual Studio Code, which let me review the code while it was being implemented:

- **Roadmap:** I defined the phases myself. `ROADMAP.md` was then written with Claude Code, and I discussed it and required changes to it before implementation began. It guided the work in small steps, each with a "Done when" list.
- **Prompt:** the first version of the system prompt was generated with AI from the manual, then revised after real conversations.
- **Code and tests:** Claude Code wrote code and tests from the roadmap's steps, while I directed the scope and decisions and reviewed the results.
- **Testing with the real model:** Claude Code ran the scenarios on the local model and read the answers. Failures became prompt or code fixes, recorded in `decisions.md` and `investigations.md`.
- **Example conversations:** these are generated by `scripts/generate_examples.py` and are not edited.
