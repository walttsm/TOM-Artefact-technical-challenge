# Empório da Música support agent

Tom is a text-message support agent for Empório da Música, a fictional musical instrument store in Campo Grande - MS. It answers in Brazilian Portuguese, looks up products, prices, promotions and orders, applies the store's policies, and registers complaints and requests for human support.

It runs as a FastAPI service with a LangGraph agent, on a local Ollama model by default or on a remote model through the OpenAI API.

| Document | What it holds |
|---|---|
| This README | How to run the project, the main decisions, assumptions, limitations and AI use |
| [decisions.md](decisions.md) | Every technical and product decision, by phase, with its reason |
| [investigations.md](investigations.md) | What was seen in real conversations with the model, what was fixed and what is still open |
| [ROADMAP.md](ROADMAP.md) | The implementation plan, step by step, with the checks for each step |
| [examples/](examples/) | Five unedited conversations with the agent |

## What the agent does

| The brief asks the agent to | How it does it |
|---|---|
| Take on a persona that fits the store | Tom, a friendly and informal assistant, defined in [system_prompt.md](app/agent/prompts/system_prompt.md) together with the store's service flow |
| Answer from the context provided | The CSVs are loaded into SQLite and the manual is split into sections. Tools read both, and each session keeps its own history |
| Know when to look up data and when to look up policies | Data tools: `buscar_produtos`, `consultar_promocoes`, `consultar_pedido` and `calcular_parcelamento`. Policy tool: `consultar_politica`, guided by an index of the manual in the prompt. Facts that apply to every message, such as address and hours, are in the prompt itself. `registrar_encaminhamento` registers complaints and requests for the team |
| Handle questions outside the store's scope | The prompt declines topics unrelated to the store and redirects accessory requests without naming other stores. Customer messages are treated as data, so attempts to change the rules are refused. Order data needs an identity check, which runs in code |

## How to run

### Setup

Requires Python 3.12 and [Ollama](https://ollama.com). The model must support tool calling. On Windows, keep the venv at a short path, because the project path is too long for some package files.

```powershell
ollama pull gemma4:12b
py -3.12 -m venv $HOME\.venvs\emporio
& $HOME\.venvs\emporio\Scripts\python -m pip install -r requirements.txt
```

### Load the data

`data/` is not in this repository on purpose. The CSVs hold customers' names, emails and phones. They are fictional, but I treated them as I would real customer data, which stays out of version control. The challenge provides the files, so they are copied in rather than downloaded.

Copy the files into `data/`, with the names they had when the challenge provided them:

```
data/
├── desafio_tecnico_ai_eng - categories.csv
├── desafio_tecnico_ai_eng - customers.csv
├── desafio_tecnico_ai_eng - order_items.csv
├── desafio_tecnico_ai_eng - orders.csv
├── desafio_tecnico_ai_eng - products.csv
├── desafio_tecnico_ai_eng - promotions.csv
└── políticas_da_loja.pdf
```

Then build the database:

```powershell
& $HOME\.venvs\emporio\Scripts\python -m app.ingest
```

This creates `storage/emporio.db` and logs the inconsistencies it finds. If a file is missing or renamed, it lists the files it expected and changes nothing. Run it again whenever the files change. It replaces the tables in one transaction and keeps registered handoffs.

The server doesn't start until the database exists.

### Run

```powershell
& $HOME\.venvs\emporio\Scripts\python -m uvicorn app.api.main:app
```

Send a message, then pass the returned `session_id` to continue the conversation:

```powershell
$body = @{ message = "Quanto custa o Takamine GD20?" } | ConvertTo-Json
Invoke-RestMethod http://localhost:8000/chat -Method Post -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

Interactive docs are at http://localhost:8000/docs, and [requests.http](requests.http) has ready-made requests.

| Endpoint | Purpose |
|---|---|
| `POST /chat` | Send `message` and an optional `session_id`. Returns `session_id` and `reply`, or 502 if the model is unavailable. |
| `GET /sessions/{id}/messages` | Read a session's history, without tool calls. |
| `GET /health` | Check that the service is up. |

### Configuration

Set these as environment variables or in a `.env` file.

| Variable | Default |
|---|---|
| `LLM_PROVIDER` | `ollama`, or `openai` for a remote model |
| `OLLAMA_HOST` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `gemma4:12b` |
| `OLLAMA_NUM_CTX` | `16384` |
| `OLLAMA_TIMEOUT` (seconds) | `300` |
| `DATA_DIR` | `data` in the project root |
| `DB_PATH` | `storage/emporio.db` in the project root |
| `CHECKPOINTS_PATH` | `storage/checkpoints.db` in the project root |
| `SESSION_TTL_DAYS` | `30` |

### Remote models through the OpenAI API

With `LLM_PROVIDER=openai`, the agent uses the OpenAI Chat Completions API, through the official SDK. `OPENAI_BASE_URL` points it at any compatible server instead of OpenAI itself.

| Variable | Default |
|---|---|
| `OPENAI_MODEL` | None: required, since model names change |
| `OPENAI_API_KEY` | None: required by OpenAI. Any text works for a local server |
| `OPENAI_BASE_URL` | OpenAI's API |
| `OPENAI_TIMEOUT` (seconds) | `60` |
| `OPENAI_CONTEXT_TOKENS` | `16384`: the budget long conversations are summarized to fit |

For example, in `.env`:

```
LLM_PROVIDER=openai
OPENAI_MODEL=<a model your account can use>
OPENAI_API_KEY=<your key>
```

To try the adapter without a key, point it at Ollama's own compatible endpoint:

```
LLM_PROVIDER=openai
OPENAI_MODEL=gemma4:12b
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_TIMEOUT=300
```

With a remote provider, the conversation leaves the machine. Emails and phones are still masked and CPF and card numbers removed, but names and everything else the customer writes are sent to the provider.

### Test

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

These follow the decisions the brief lists. [decisions.md](decisions.md) records each one in detail, and [investigations.md](investigations.md) records the model behavior that led to several of them.

### Architecture

```
FastAPI -> LangGraph agent -> tools -> data store (SQLite): what the facts are
                                    -> policy (pure Python): what the rules allow
```

Facts and rules are separate layers that never import each other, and only the tools combine them. Rules such as "a promotion cancels the PIX discount" are plain functions, so they can be tested without a database or a model.

### Agent approach: function calling with fixed tools

The questions the store receives are known, and a wrong answer about a price or an order is expensive. Fixed tools give the model no way to read another customer's data or to do its own price arithmetic, as a free-form SQL agent would. Identity checks and prices run in code, so a prompt injection can't talk its way past them.

### Framework: LangGraph

The agent is a small explicit graph: `compact -> agent <-> tools`, with a round limit, where `compact` summarizes long conversations. LangGraph supplies the checkpoints and per-session threads. The turn in progress is kept apart from the finished history, so a model failure partway through a turn leaves the session intact.

### Retrieval: section lookup, not embeddings

The manual is short and already numbered, so the prompt holds an index and `consultar_politica` returns a whole section. This needs no embedding model or vector store, and the model always sees a complete rule rather than a fragment. The cost is that the model has to choose the right section.

### Model and provider: Ollama and `gemma4:12b`, with an OpenAI-compatible option

A local model has no API cost or key, so anyone can run the project, and customer data stays on the machine. The trade-off is slower and less reliable answers, which is why guarantees live in code wherever possible. `gemma4:12b` handled the test scenarios at about 3 to 20 seconds per message on the development machine. A systematic comparison with other models is still to be done.

The model is behind a small adapter, a function from messages and tools to a reply, so a remote model can be used instead. `LLM_PROVIDER=openai` selects an adapter for the OpenAI Chat Completions API, which most providers and local servers accept. It is there as an example of an external API integration. The scenarios were run on it only through Ollama's compatible endpoint.

### Prompt strategy

The prompt holds what applies to every message: persona, service flow, store details, when to use each tool, what must never be invented, scope and privacy. Case-specific rules come from the manual on demand. The prompt was revised after reading real conversations, and [investigations.md](investigations.md) records the failures behind those changes.

### Interface: an HTTP API

A support agent is reached through a messaging channel, so a service that a channel such as WhatsApp could call fits better than a CLI or a notebook. FastAPI also generates interactive docs, which serve as a simple interface for trying the agent.

### Conversation history: persisted in SQLite, summarized when long, expired when idle

Support conversations continue over time, so sessions survive a server restart through LangGraph's `SqliteSaver`. Checkpoints sit in a separate file from the store's data.

Ollama drops the start of a prompt that doesn't fit `num_ctx`, without warning. Before each turn, the agent estimates the context, and above 60% of `num_ctx` it folds the oldest turns into a summary written by the model, keeping the two most recent turns whole. The summary leaves out prices and order status, which the model looks up again. The history the API returns stays complete.

A session idle for 30 days is deleted.

### Data treatment

- **Ingestion:** files are read as UTF-8, empty values become `NULL`, phones are stored as digits and emails in lowercase, and accent-free columns make "violao", "Violão" and "violões" find the same products.
- **Quality:** inconsistencies are logged at ingestion and the source values are kept. Each one has a defined treatment, listed under [Assumptions](#assumptions).
- **The manual:** the PDF is parsed into numbered sections, page headers and footers are removed, and its contradictions are corrected explicitly.
- **Personal data:** emails and phones are replaced by readable masks, such as `a***@coldmail.com`, before a message reaches the graph. The real values stay in memory and are put back only when a tool runs, so the model, the checkpoints and the history never hold them. CPF and card numbers are removed, since no tool needs them.

## Example conversations

The five conversations in [examples/](examples/) were generated by [scripts/generate_examples.py](scripts/generate_examples.py) with `gemma4:12b`, and are not edited. Each shows the tools the agent called.

| Conversation | What it shows |
|---|---|
| [01 Catálogo e preço](examples/01_catalogo_e_preco.md) | Guitars up to R$ 1.000, the Takamine GD20's exact prices, and a sold-out model with alternatives |
| [02 Informações e fora de escopo](examples/02_informacoes_e_fora_de_escopo.md) | The store's address with no tool, an accessory request redirected, and an unrelated question declined |
| [03 Devolução](examples/03_devolucao.md) | **Policy rules:** the agent asks for the purchase channel and date before reading the return policy and applying it |
| [04 Promoção expirada e pedido](examples/04_promocao_expirada_e_pedido.md) | **Live data:** an expired Black Friday with the current price, the active promotions, and an order lookup with an identity check |
| [05 Privacidade e reclamação](examples/05_privacidade_e_reclamacao.md) | A request for another customer's orders and a prompt injection refused, and a complaint registered with a protocol number |

The tool calls show what the model sent, so the contacts in examples 04 and 05 appear masked, such as `a***@coldmail.com`. The tools received the real values. Two answers show limitations recorded in [investigations.md](investigations.md): in example 04, the agent says the Páscoa promotion ended without saying that the product was never in Black Friday, and it reports a past delivery estimate without flagging it as overdue.

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
- Customer names are kept in the conversation on purpose. A customer who gives their name and is later called by it shows that the model keeps the context, and masking the name would hide that. No tool returns a name, so a name only appears if the customer types it.
- The agent answers what the data and the manual support, and says so when it can't help, rather than estimating. For shipping prices and the other gaps below, it points the customer to the store's contact channels.

## Known limitations and what I would do with more time

### What was not implemented, and how it would be

| Gap | In this prototype | Proposed solution |
|---|---|---|
| Shipping price and delivery estimate | The agent explains the delivery rules in section 5 of the manual, but doesn't quote a price or a date for an address, and points the customer to the store's contact channels | A `cotar_frete` tool that sends the postal code and products to an external API with the logistics partners' fares and rules, while the policy layer applies the store's own shipping rules to the result |
| Real delivery status and overdue orders | The agent reports the recorded estimate and tracking code, and doesn't flag an estimate that has passed | Query the carrier's tracking API by tracking code, and compare the estimate with today's date in the tool, so the model is told when an order is late |
| Typos in product search | Keyword search that ignores accents and plurals | Fuzzy matching, such as SQLite's FTS5 trigram index, or embeddings for "similar products". For this process, the current search is enough: the model usually sends clean terms, and every result carries its category |
| Holidays | The prompt says the store is closed on holidays, but has no list of which dates they are | A holiday table kept by the store, or a public holiday API, read by a tool when the customer asks about hours |
| Purchase channel | Orders don't record where they were bought, so the agent asks before applying the return policy | Take the channel from the order system and add it to the order record |
| Handoffs | Saved with a protocol number, but nobody is notified, and a retry after a model failure can register one twice | Send each handoff to the team's helpdesk or chat through a webhook, with an idempotency key per turn |
| Security | No authentication or rate limiting, so anyone with a `session_id` can read that conversation. Identity checks are basic | Tie sessions to the messaging channel's verified user, put the API behind a gateway with rate limits, and strengthen identity checks, for example with a one-time code |
| Names in messages | Kept on purpose, as explained under [Other assumptions](#other-assumptions) | Mask names with a Portuguese named-entity model, since a name has no fixed pattern |
| Model choice | `gemma4:12b`, with each scenario run only a few times | An automated evaluation that replays the scenarios over repeated runs and checks tool calls and answers, used to compare local and remote models |

### Other known limitations

- **Model reliability:** answers vary between runs. [investigations.md](investigations.md) lists what was seen and what is still open.
- **Follow-up questions:** the model can reuse earlier tool results, so prices in a long conversation can be out of date.
- **Context size:** the context is estimated from characters, not counted, and a single turn with many large tool results is not summarized until the next turn.
- **Search:** it matches words in descriptions, so a search can include a related product. A term combining a campaign and a product returns nothing.
- **Masked contacts:** the real values are held in memory by a single server process. After a restart, the customer has to give their contact again. [Scaling in production](#scaling-in-production) covers a shared store.

### Scaling in production

The prototype answers each message inside its HTTP request, in a single process. A local model on one machine answers one message at a time and takes 3 to 20 seconds, so production traffic would need a different setup:

- **A message queue:** the channel's webhook puts each message on a queue, such as RabbitMQ or a managed one like Amazon SQS, and returns at once. Workers take messages from the queue, run the agent and send the reply through the channel. Messages from one session are processed in order, failed ones are retried, and bursts wait in the queue instead of timing out.
- **Horizontal scaling and load balancing:** workers scale with the length of the queue, and Ollama instances run on GPU machines behind a load balancer. The exact design depends on the cloud chosen. On AWS, for example, this could be GPU instances in an Auto Scaling group, or containers on ECS or EKS.
- **Shared state:** any worker must be able to serve any session. Checkpoints would move from SQLite to a shared database, for which LangGraph has a PostgreSQL saver. The masked contacts held in memory would move to a shared store with expiry, such as Redis.
- **With a remote provider:** model capacity and load balancing become the provider's responsibility, within its rate limits. The queue is still useful: it absorbs bursts, retries calls that fail or hit a rate limit, and keeps the service available when the provider is slow.
- **Scheduled jobs:** ingestion of new data and the scan for expired sessions could run in a workflow orchestrator such as Apache Airflow, which suits scheduled jobs better than processing individual messages.

### Also with more time

- Add streaming replies and a simple chat interface.
- Package the API with Docker.

## Use of coding assistants

I used Claude Code throughout, for planning, implementing and reviewing. I ran it inside Visual Studio Code, which let me review the code while it was being implemented:

- **Roadmap:** I defined the phases myself. [ROADMAP.md](ROADMAP.md) was then written with Claude Code, and I discussed it and required changes to it before implementation began. It guided the work in small steps, each with a "Done when" list. Phases 6 and 7, for personal data, long conversations and the OpenAI adapter, were added the same way after the first five.
- **Prompt:** the first version of the system prompt was generated with AI from the manual, then revised after real conversations.
- **Code and tests:** Claude Code wrote code and tests from the roadmap's steps, while I directed the scope and decisions and reviewed the results. Each step is one commit.
- **Testing with the real model:** Claude Code ran the scenarios on the local model and read the answers. Failures became prompt or code fixes, recorded in [decisions.md](decisions.md) and [investigations.md](investigations.md).
- **Example conversations:** these are generated by `scripts/generate_examples.py` and are not edited.
