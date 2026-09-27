# Multi-Agent Customer Support for a Digital Music Store

## Project Overview

Build a web-based customer support assistant for a digital music store. The application must use a LangGraph multi-agent workflow to coordinate specialist agents, query a real Chinook SQLite database, verify customer identity before exposing account data, and remember explicit music preferences across sessions.

The assistant must be grounded in database results. It must never invent catalog, invoice, customer, or employee information.

## Quick Start

### 1. Prerequisites

- Python 3.12

Verify installation:

```bash
python3.12 --version
```

### 2. Create a virtual environment

```bash
python3.12 -m venv .venv
```

### 3. Activate the virtual environment

```bash
source .venv/bin/activate
```

Tip: Your shell prompt shows `(.venv)` when active. Deactivate anytime with `deactivate`.

### 4. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 5. Load DB in memory and run a smoke test

This project provides a tiny CLI to download/cache the Chinook SQL and load it into an in-memory SQLite database, then run basic checks.

```bash
# Health check (verifies required tables and a simple query)
python -m src.database.cli health

# Smoke test (prints total artists and a small sample)
python -m src.database.cli smoke
```

Notes:
- The first run downloads the Chinook SQL to `.cache/Chinook_Sqlite.sql` (configurable via `DATABASE_CACHE_PATH`).
- Subsequent runs reuse the cached file.

### 6. Run tests

```bash
# Focused database + config tests
pytest tests/test_database.py tests/test_config.py -v

# Full suite
pytest tests/ -v
```

## Goals

- Route music catalog questions to a Music Catalog Agent.
- Route invoice and account questions to an Invoice Agent.
- Use a supervisor to handle music, invoice, mixed, and off-topic requests.
- Require identity verification before account-specific information is available.
- Persist customer preferences and merge new preferences with existing ones.
- Use parameterized SQL for every database query.
- Provide a usable Gradio chat interface with isolated browser sessions.

## Expected Technology

- Python 3.12
- LangGraph and `langgraph-supervisor`
- LangChain and `langchain-openai`
- OpenAI-compatible `ChatOpenAI` provider
- SQLite in-memory database with SQLAlchemy
- Pydantic v2
- Gradio Blocks
- `python-dotenv`
- pytest

## Milestone 1: Database Foundation

Follow these steps before building any agent:

1. Create a Python 3.12 virtual environment and install the dependencies from `requirements.txt`.
2. Copy `.env.example` to `.env` and set the LLM values when agent work begins.
3. Use `src.database.connection.create_in_memory_engine()` to create the shared SQLite engine.
4. Call `load_chinook_database(settings.database_cache_path)` during application startup. The first call downloads the raw Chinook SQL file; later calls reuse the cached file.
5. Use `src.database.queries.execute_query(engine, sql, params)` for all reads. Pass user values through `params`; never interpolate them into SQL.
6. Use `normalize_phone(value)` for both stored and incoming phone values before identity comparisons.
7. Call `database_health_check(engine)` after loading and fail startup if it returns `False`.
8. Run the focused tests with `pytest tests/test_database.py tests/test_config.py -v`.

The database foundation currently exposes these functions:

```python
from src.database import (
	database_health_check,
	execute_query,
	load_chinook_database,
	normalize_phone,
)
```

The foundation tests cover parameter binding, empty results, JSON serialization, cache reuse, required-table health checks, phone normalization, and environment configuration. Keep this layer independent from LangGraph so it can be tested without an LLM or network access.

## Functional Requirements

### 1. Multi-Agent Orchestration

Create a supervisor graph with two specialist agents:

#### Music Catalog Agent

Handles:

- Albums by artist name using fuzzy matching.
- Tracks by artist name, including total count and a representative sample.
- Songs by genre, sampled across different artists.
- Specific songs by fuzzy title matching.
- Complete details for a track ID.

#### Invoice Agent

Handles account-specific requests after verification:

- All customer invoices, sorted newest first.
- Purchased tracks across all invoices, sorted by price.
- Support representative for a specific invoice.
- Detailed line items for a specific invoice.

#### Supervisor

The supervisor must:

- Route catalog requests to the Music Catalog Agent.
- Route account and billing requests to the Invoice Agent.
- Route mixed requests to both agents when necessary.
- Reject off-topic requests directly without calling a specialist.
- Preserve verification and preference state while routing.

Every agent must follow these grounding rules:

1. Call a tool before answering factual questions.
2. Use only information returned by tools.
3. Quote exact counts, dates, prices, and identifiers.
4. Report empty results honestly.
5. Mention when a result is sampled or truncated.
6. Defer requests outside the agent's scope.

### 2. Identity Verification

Before invoice, purchase, billing, or support-representative data is returned, verify the customer with one of:

- Numeric customer ID.
- Email address, compared case-insensitively.
- Phone number, normalized by removing formatting characters before comparison.

Verification behavior:

- Look up the identifier in the `Customer` table.
- If the lookup fails, ask for another identifier.
- On success, store the verified numeric customer ID in graph state.
- Downstream agents must read the customer ID from trusted state only.
- Downstream agents must never extract a customer ID from free-text messages.
- Use an interrupt/resume flow for missing or failed verification.

### 3. Preference Memory

Maintain a preference store scoped by verified customer ID.

Save explicit statements such as:

- `I love rock.`
- `AC/DC is my favorite.`

Do not save questions such as `Do you have rock music?` as preferences. New preferences must be merged with existing preferences and must never remove older values. Load preferences when a verified customer begins a new interaction and make them available to the Music Catalog Agent.

### 4. Data Safety

- Load the Chinook SQL file into an in-memory SQLite database at startup.
- Cache the downloaded SQL file locally.
- Use SQLAlchemy `text()` with named bind parameters for all queries.
- Never construct SQL with string concatenation or f-strings.
- Validate numeric tool inputs before querying.
- Normalize phone values consistently.
- Return tool results as valid JSON.

### 5. Chat Interface

The Gradio interface must:

- Display user and assistant messages as a chat thread.
- Create a unique thread/session ID for each browser tab or user.
- Show processing, success, and error states.
- Pause cleanly while identity verification is waiting for input.
- Resume the graph after the user supplies an identifier.
- Provide a new conversation/reset action.

## Suggested Project Skeleton

Use this structure as the implementation starting point:

```text
.
├── app.py
├── requirements.txt
├── Dockerfile
├── .env.example
├── README.md
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── connection.py       # Download, cache, and load Chinook
│   │   ├── queries.py          # Parameterized SQL query helpers
│   │   └── models.py           # Optional typed result models
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── catalog.py          # Music catalog tools
│   │   ├── invoices.py         # Invoice and purchase tools
│   │   └── identity.py         # Customer verification tools
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── prompts.py          # Scope and grounding instructions
│   │   ├── catalog_agent.py
│   │   ├── invoice_agent.py
│   │   └── supervisor.py       # LangGraph supervisor and routing
│   ├── memory/
│   │   ├── __init__.py
│   │   └── preferences.py      # Per-customer preference persistence
│   ├── state/
│   │   ├── __init__.py
│   │   └── graph_state.py      # Verified ID, memory, messages, interrupts
│   └── ui/
│       ├── __init__.py
│       └── chat.py              # Gradio Blocks interface
└── tests/
		├── conftest.py
		├── test_database.py
		├── test_identity.py
		├── test_catalog_tools.py
		├── test_invoice_tools.py
		├── test_memory.py
		├── test_routing.py
		└── test_json_outputs.py
```

## Recommended State Shape

Keep trusted account data separate from user message text:

```python
class SupportState(TypedDict):
		messages: list
		thread_id: str
		verified_customer_id: int | None
		verification_pending: bool
		preferences: dict[str, list[str]]
		pending_request: str | None
```

Only the identity verification node may populate `verified_customer_id`. Invoice tools should accept the trusted ID as an explicit argument and reject missing or invalid values.

## Tool Contract Checklist

Each tool should:

- Accept validated, typed arguments.
- Execute a parameterized query.
- Return a JSON-serializable object or JSON string.
- Return an explicit empty result when no rows match.
- Include totals when the result is sampled.
- Avoid leaking data belonging to another customer.

Suggested catalog tools:

```text
search_albums_by_artist(artist_name)
search_tracks_by_artist(artist_name, sample_limit)
browse_tracks_by_genre(genre_name, sample_limit)
search_track_by_title(title, sample_limit)
get_track_details(track_id)
```

Suggested invoice tools:

```text
get_customer_invoices(verified_customer_id)
get_customer_purchased_tracks(verified_customer_id)
get_invoice_support_rep(verified_customer_id, invoice_id)
get_invoice_line_items(verified_customer_id, invoice_id)
```

## Conversation Flows

### Verified Account Request

```text
User asks for invoices or purchases
	-> Supervisor detects account scope
	-> Verification node checks trusted state
	-> If unverified, interrupt and request ID, email, or phone
	-> Identity tool verifies against Customer
	-> Store numeric customer ID in state
	-> Invoice Agent calls its database tool
	-> Supervisor returns grounded response
	-> Preference extractor merges explicit preferences
```

### Catalog Request

```text
User asks about music
	-> Supervisor routes to Music Catalog Agent
	-> Catalog Agent calls the appropriate catalog tool
	-> Agent reports exact results, counts, and sampling limits
```

### Off-Topic Request

```text
User asks about an unrelated topic
	-> Supervisor declines directly
	-> No specialist agent or database tool is called
```

## Testing Requirements

Aim for at least 25 automated tests covering:

- Catalog tools: successful searches, empty results, fuzzy matching, and invalid IDs.
- Invoice tools: valid lookups, missing invoices, customer isolation, line items, and date ordering.
- Database utilities: query execution, health check, SQL loading, and phone normalization.
- Identity: ID, case-insensitive email, normalized phone, invalid identifiers, and verification state.
- Memory: explicit statements, questions excluded, merge behavior, customer scoping, and persistence.
- Routing: catalog, invoice, mixed, and off-topic requests.
- Determinism: repeated genre queries return the same ordered results.
- JSON validity: every tool output parses successfully.

Run the test suite with:

```bash
pytest tests/ -v
```

## Configuration

Create `.env` from `.env.example`. Include configuration for:

```dotenv
LLM_MODEL=
LLM_API_KEY=
LLM_BASE_URL=
LLM_TEMPERATURE=0
DATABASE_CACHE_PATH=.cache/Chinook_Sqlite.sql
HOST=0.0.0.0
PORT=7860
```

Do not commit `.env`, API keys, tokens, or the downloaded Chinook SQL file.

## Running the Application

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python app.py
```

Open the local Gradio URL printed by the application.

## Docker

The Dockerfile should:

1. Use a Python 3.12 base image.
2. Install `requirements.txt`.
3. Copy the application source.
4. Expose the configured Gradio port.
5. Start the app with `python app.py`.

Example commands:

```bash
docker build -t digital-music-support .
docker run --env-file .env -p 7860:7860 digital-music-support
```

## Delivery Checklist

- [ ] Application starts with `python app.py`.
- [ ] Chinook data downloads once and loads into SQLite memory.
- [ ] Supervisor routes catalog, invoice, mixed, and off-topic requests.
- [ ] Catalog tools return grounded, diverse, deterministic results.
- [ ] Identity verification supports ID, email, and normalized phone.
- [ ] Account tools require trusted verified customer state.
- [ ] Preferences persist per customer and merge without data loss.
- [ ] All SQL is parameterized and numeric inputs are validated.
- [ ] Gradio sessions are isolated and resettable.
- [ ] At least 25 tests pass with `pytest tests/ -v`.
- [ ] `Dockerfile`, `.env.example`, `requirements.txt`, and setup documentation are included.
- [ ] No secrets or actual `.env` file are committed.