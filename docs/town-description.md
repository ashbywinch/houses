# Town Description

**Module**: `houses/town_desc.py` — `generate_town_description()`

**Purpose**: Generate a single-sentence, honest description of a neighbourhood for someone choosing where to buy a home.

**How it works:**
1. Calls OpenRouter chat completions (`POST /api/v1/chat/completions`).
2. System prompt instructs the model to be specific and balanced (mentions trade-offs, no marketing fluff).
3. Output is truncated to the first sentence.
4. Results cached in-memory by town name.

**Prompt rules:** Exactly one sentence, no markdown, honest about trade-offs, no prices/transport/schools (separate columns), no repeating the area name.

**Configurable via settings:** `llm_model`, `llm_temperature`, `llm_max_tokens`.

**Columns populated:** Area Description

**Graceful degradation:**
- Returns empty string if no API key configured.
- Logs warning and returns "" on any API failure.
