# Decisions

## Phase 2: Extract the prompt

### Keep the full policy text in the prompt until Phase 4

The roadmap asks for an index of the case-specific sections, and also for the full policy text until Phase 4. The assistant has no `consultar_politica` tool yet, so an index alone would leave it unable to answer policy questions. The text sits under `# Políticas da loja`, so Phase 4 can replace that block with the index.

### Resolve the manual's contradictions in the prompt

| Contradiction | Decision |
|---|---|
| WhatsApp (67) 3341-4444 vs (67) 3321-4500 | 3341-4444 |
| `contato@emporiodamusica` vs `contato@empóriodamusica` | No accent |
| R$ 100 minimum installment vs tiers in section 3.1 | Tiers in 3.1 |
| "No minimum (except below R$ 50)" for up to 3x | R$ 50 minimum |

These follow the roadmap's table in section 3.3. Tests check them, so a later prompt edit can't bring the wrong values back unnoticed.

### Name the persona "Tom"

The name comes from the earlier build. It isn't in the manual.

### Don't name other stores

The manual says to suggest partner stores when possible, but no list of them exists. Naming one would mean inventing it, so the prompt redirects accessory requests without naming any store.

### Present campaigns as typical, not active

The manual lists campaigns such as Black Friday by month. The assistant can't check active promotions yet, so the prompt labels the list as campaigns that usually happen and forbids saying that any promotion is active.

### Say that this conversation doesn't reach the team

In the first real-model run, `gemma4:12b` told customers the team would read and follow up on their messages. Nothing sent here reaches the team, so the prompt now forbids that promise and points customers to the contact channels. The rule should be revisited in Phase 4, when `registrar_encaminhamento` can create real handoffs.

### Mention opening hours only when relevant

The first run also added closed-store notices to unrelated answers, such as a question about picks. The assistant now says when the store reopens only if the customer asks about hours or needs to talk to the team.

## Phase 3: Create tooling

### Keep the source data and define a treatment for each inconsistency

The CSVs are the only record of what happened, and nothing in them shows which conflicting value is right. Overwriting a value would be a guess that looks like a fact. So ingestion keeps the source values, the quality report logs each case with its treatment, and the rule is applied in the layer that owns it. The sections below say why, and the roadmap lists the later steps that apply each rule.

### Order totals differ from their items (orders 3 and 20)

- **Seen:** Order 3 has a total of R$ 3.450,00 and items worth R$ 3.498,00. Order 20 has a total of R$ 1.400,00 and items worth R$ 1.488,00.
- **Treatment:** The recorded total stands, and `consultar_pedido` shows no item prices.
- **Why:** The data has no price history, so the comparison uses today's prices. In both orders the total is below the items, which fits a past discount as well as an error. The total is what the customer agreed to pay, and showing today's prices beside it would create a conflict for the customer that may not exist.

### Unavailable products with stock (products 130 and 136)

- **Seen:** Product 130 is `coming_soon` with 2 units. Product 136 is `discontinued` with 13 units.
- **Treatment:** The stock stays as recorded, and `product_situation` lets status win.
- **Why:** Stock counts physical units, while status is the store's decision to sell. Units can exist before a launch or after a product is withdrawn, so the two values don't really conflict. Selling these units on the strength of the stock number would override that decision.

### Empty categories (categories 6, 7 and 8)

- **Seen:** The wind instrument and orchestral string categories have no products.
- **Treatment:** The categories stay, and a search in them returns nothing, so the assistant says there are no products right now.
- **Why:** The manual still mentions wind instruments (section 4.4, mouthpieces), so these look like real lines with no current stock. Deleting them would turn "not available now" into "the store doesn't sell this", and the data doesn't support that.

### Customers sharing an email (customers 11 and 43)

- **Seen:** Both use `bruno.martins@jmail.com`, with similar names, the same city and different phones. Customer 11 has order 10.
- **Treatment:** Both records stay, marked with `email_is_shared`. Phones must be unique. `can_view_order` won't accept a shared email on its own, so the phone is required.
- **Why:** They may be one person, but the data can't prove it, and wrongly merging them would expose one customer's orders to another. With an order number, one matching contact is normally enough, which would let customer 43 open order 10. Privacy matters more here than convenience, and all 50 phones are distinct, so asking for the phone settles it.

### Orders containing a product not yet released (orders 3 and 14)

- **Seen:** Both contain product 130, which is `coming_soon`. Order 3 is `delivered`, and order 14 is `pending`.
- **Treatment:** Both orders stay, and the assistant reports the recorded status and promises no delivery date for order 14.
- **Why:** Status is a current value with no history, so a product can have been sold earlier and then relisted. That can't be verified, but it doesn't justify rewriting an order. Order 14 has no estimated delivery date, so a promised date would be invented.

### Orders below the installment minimum (orders 9, 16 and 18)

- **Seen:** Installments are R$ 58,32 on order 9 (6x) and R$ 71,65 on order 16 (6x), against a R$ 80 minimum. Order 18 has R$ 38,32 (12x), against a R$ 100 minimum.
- **Treatment:** The orders stay as recorded, and the current rules apply only to new quotes. The automatic check waits for step 3.4.
- **Why:** The rules may have changed since these orders were placed, and the assistant shouldn't tell a customer that their payment was invalid. The minimums belong to `policy`, which `store` can't import, so `ingest.py` will run this check once `installment_plan` exists.

### Repeated and off-range promotions

- **Seen:** Product 93 has "Promoção de Páscoa" at 15% and 10%, and product 96 has "Semana do Músico" at 18% and 40%. Two others fall outside the manual's ranges: promotion 1 (Black Friday, 10%, range 15% to 30%) and promotion 17 (Aniversário, 30%, range 10% to 25%). All of them are inactive. The active "Desconto Exclusivo" and "Oferta Especial" aren't in the manual's campaign list.
- **Treatment:** Every row stays, and `consultar_promocoes` shows an expired promotion once, with no percentage. Active promotions apply as recorded.
- **Why:** Repeated rows can be separate editions of a campaign. Expired percentages have no use for the customer and could read as an offer. `is_active` is the only record of what is on sale now, so it decides over the manual's list of campaigns.

### Overdue orders aren't flagged

- **Seen:** Order 8 is `shipped` with an estimated delivery of 2026-02-28, and pending orders date from March 2026.
- **Treatment:** The assistant reports the recorded status and dates, and offers `registrar_encaminhamento` if the customer complains. The quality report doesn't check for this.
- **Why:** The answer depends on today's date, and the data is a snapshot that may be old. Including it would also make two ingestion runs give different reports.

### Allow one active promotion per product

Some products have several past promotions, but none has two active ones. A partial unique index keeps it that way, so joining the active discount in step 3.2 can't return a product twice.

### Check foreign keys after loading

Enforcement stays off during the load, and `PRAGMA foreign_key_check` runs before the commit. This reports every broken reference at once, and any failure rolls back to the previous data.

### Handle "-ão" plurals when searching

"violao" is not a substring of "violoes", so accent removal alone can't meet the roadmap's search requirement. Each search word is expanded into its singular and plural forms: `-ão`, `-ões` and `-ães` for those words, and dropping the final "s" for regular plurals. This covers the store's vocabulary without adding a stemming library.

### Return every match so the tool can filter by final price

`search_products` accepts `limit=None`. The scenario "violões até R$ 1000" must filter on the final price, and that calculation belongs to `policy`, so the store can't do it in SQL. The default limit of 10 on 36 matches would otherwise drop cheap products before the filter runs.

### Keep `available_only` in SQL

Deciding availability belongs to `policy`, but the roadmap puts this filter in the query, and applying it before `LIMIT` is what keeps the limit correct. It repeats the rule in `product_situation`, so a test in step 3.4 checks that the two agree.

### Add a search column to promotions

`find_promotions` must find "Promoção de Páscoa" from "pascoa", which needs the same accent-free text as products and categories.

### Keep handoffs out of the rebuild

The `handoffs` table is the only one created with `IF NOT EXISTS` and not dropped by `schema.sql`. Handoffs happen at runtime and exist nowhere else, so re-ingestion must not erase them.

### Correct two more statements in the manual

Besides the roadmap's four contradictions, the parser removes two statements, because `consultar_politica` would otherwise hand them to the model:

| Statement | Decision | Why |
|---|---|---|
| "Mais de 300 instrumentos" | Removed | The catalog has 65 products, and the assistant could repeat a number the data contradicts. |
| "Sugerir lojas parceiras quando possível" | Removed | No list of partner stores exists, and the prompt already forbids naming stores. |

Each correction targets one section, so the valid "parcela mínima de R$ 100,00" in section 3.1 stays while the same phrase in section 3 changes. A correction that stops matching logs a warning, and a test fails if that happens with the current manual.

### Return subsections with their section

Asking for section "5" also returns 5.1, 5.2 and 5.3, because most of the rules sit in subsections and the top-level text alone would look like a complete answer.

### Allow one payment below R$ 50

The manual's lowest tier sets a R$ 50 minimum for "up to 3x", but a R$ 30 purchase must still be payable, and the roadmap expects 1x for it. The minimum therefore applies only from 2x up.

### Check installment minimums on the exact division

R$ 479,99 in 6x is R$ 79,998, which rounds to the R$ 80 minimum. Checking the rounded value would allow a plan below the minimum, so the check uses the unrounded division and rounding applies only to the value shown.

### Tell `can_view_order` how the order was found

The roadmap's rule differs for lookups with and without an order number, and the record alone can't show which applies. The function takes `by_order_number`, which is `True` by default and set to `False` for lookups by contact.

### Accept a Brazilian country code in phones

Customers may type `+55`, while stored phones have only the area code and number. `normalize_phone` removes the country code so the identity check compares the same format. Missing area codes are not filled in, because guessing one could match another customer.

### Add a price limit to `buscar_produtos`

The roadmap's tool has no budget argument, but the Phase 5 scenario "violões até R$ 1000" needs one. Without it, the model would have to pick from the first 10 of 35 products in alphabetical order and would miss cheaper ones. The limit compares the final price, so a product on promotion counts at its promotional price, and products with hidden prices are left out.

### Include installments in product results

The roadmap's result for `Takamine GD20` includes "12x of R$ 183,25". Returning it from `buscar_produtos` saves a second tool call and stops the model from doing the division itself.

### Keep sold-out prices visible

The roadmap hides prices only for `descontinuado` and `em_breve`. A sold-out product is still in the catalog and may return, so its price stays visible and the situation shows it can't be bought now.

### Return formatted money as text

Tools return values such as "R$ 2.199,00". Local models are likely to reformat or round raw numbers, and the text can be copied as it is.

### Leave customer data out of order results

`consultar_pedido` returns no name, email or phone, even after a successful check. The customer already gave a contact, and the model doesn't need the rest to report on the order.

### Show expired promotions through one lookup

`consultar_promocoes` takes only a term. Without one it lists active promotions, and with one it adds matching expired ones. This covers "Ainda tem Black Friday?" without the model having to decide on an `include_expired` argument, and an expired entry carries the current price instead of the old discount.

### Keep the tools unregistered until Phase 4

`StoreTools` isn't connected to the graph yet, as planned in step 4.1, so the current prompt's statement that the assistant can't check the store's systems is still true.

## Phase 4: Connect the assistant to the tools

### Make the LLM return a message

The `LLM` type took messages and returned text, so it couldn't express a tool call. It now takes messages and tools, and returns an assistant message with an answer, tool calls or both. Messages use Ollama's chat format in plain dicts, because Ollama's response objects would have to be stored in the checkpoints. An empty answer with no tool calls still counts as a failure, while an empty answer with tool calls is normal.

### Declare one type per tool argument

The tools accepted types such as `list[str] | str | None`, and Ollama's schema converter turned that into `"array, string"`, which isn't a valid JSON Schema type. It also marked `limite` and `apenas_disponiveis` as required because their defaults weren't `None`. The signatures now state only what the model is asked to send, and all optional arguments default to `None`. Normalization stays, since local models still send loose values, and a test pins the schemas.

### Keep the turn in progress apart from the history

The state has `messages` for finished turns and `turn` for the current one. Tool calls enter `turn` during the turn, and `finish_turn` moves them to `messages` once there is an answer. A new question overwrites `turn`, so a turn that failed halfway leaves nothing behind, including in the checkpoint database.

### Keep tool results in the conversation context

Finished turns keep their tool calls and results, so a follow-up such as "em quantas vezes posso parcelar?" needs no new lookup. `history()` hides them from the API. The cost is a larger context on long conversations, already listed as a known limitation.

### End the turn at the round limit without another model call

`MAX_TOOL_ROUNDS` is 5, and the conversations so far needed at most 2 tool calls per turn. Reaching the limit returns a fixed message with the WhatsApp number. The check runs after the tools, so every stored call has its result and the session's next message is still valid.

### Send every tool error to the model

Unknown tools, wrong arguments and exceptions all return as `Erro ao executar ...`, with a warning in the log. The model can fix its arguments or tell the customer it couldn't check. Only LLM failures stop the turn, with a 502.

### Open the checkpoint database at server startup

`app = create_app()` runs on import, so opening SQLite there would create `storage/checkpoints.db` whenever the tests import the API. The lifespan handler opens it when the server starts and closes it when it stops.

### Register complaints without an order number

In the first run with tools, `gemma4:12b` asked for an order number before registering a complaint from a customer who had already given a phone. The prompt now says a contact is enough, and the next run returned a protocol number.

### Treat a budget of zero as no budget

`qwen3:8b` sent `preco_maximo: 0` with an unrelated question. This filtered out every product, and the assistant said the Martin D-28 was out of stock, so `buscar_produtos` now ignores a zero budget.

### Replace "this conversation doesn't reach the team"

The Phase 2 rule was due for review now that handoffs exist. The team receives what `registrar_encaminhamento` records and nothing else, and the prompt says so.

### Keep `gemma4:12b` as the default model

All six installed models report the `tools` capability, and `gemma4:12b` and `qwen3:8b` were run with tools. `gemma4:12b` used the correct tools with exact values. `qwen3:8b` also called the tools, but it didn't register a complaint and offered a notification the assistant can't send. The full comparison is part of Phase 5.
