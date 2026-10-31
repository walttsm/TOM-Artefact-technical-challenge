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
