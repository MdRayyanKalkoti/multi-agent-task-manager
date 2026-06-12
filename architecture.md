# Architecture — Multi-Agent AI Task Management System

## 1. System overview

A single FastAPI process hosts four cooperating subsystems: the REST API, the HTML dashboard, the Telegram bot (long polling), and the APScheduler job runner. All intelligence is organised as six specialised agents that share one SQLite database through SQLAlchemy and one Gemini 2.5 Flash client.

```
                          ┌──────────────────────────────────────────────┐
                          │              FastAPI process                 │
                          │                                              │
 Telegram user ──polling──►  TelegramService ─┐                          │
 Browser ────HTTP /api────►  REST routes ─────┤                          │
 Browser ────HTTP / ──────►  Dashboard (Jinja)│                          │
 Cron (APScheduler) ──────►  Scheduler jobs ──┤                          │
                          │                   ▼                          │
                          │        ┌────────────────────┐                │
                          │        │ ORCHESTRATOR AGENT │◄── Memory Agent│
                          │        └─────────┬──────────┘    (context)   │
                          │      ┌───────────┼─────────────┐             │
                          │      ▼           ▼             ▼             │
                          │  Task Agent  Priority Agent  Recommendation  │
                          │      │           │            Agent          │
                          │      │           └────► Gemini 2.5 Flash ◄───┤
                          │      ▼                                       │
                          │   SQLite (SQLAlchemy ORM)  ◄── Reminder Agent│
                          └──────────────────────────────────────────────┘
```

## 2. The six agents

| Agent | Responsibility | Talks to |
|---|---|---|
| **Orchestrator** | Parses natural language into a structured intent (Gemini, with a deterministic regex fallback), routes it, composes the reply. The only public entry point for conversational input. | Memory, Task, Priority, Recommendation agents; Gemini |
| **Task** | Sole owner of task persistence: create, update, complete, delete, list, search, filter, overdue queries, statistics. No other agent touches the `tasks` table directly. | SQLite |
| **Priority** | Assigns `low/medium/high/urgent`. Keyword + deadline heuristics run first; Gemini is consulted only for ambiguous (default-medium) cases, keeping latency and cost low. | Gemini (optional) |
| **Recommendation** | Scores open tasks (priority weight + deadline pressure + staleness), then asks Gemini to phrase a short coaching message; falls back to a templated sentence. | Task agent; Gemini (optional) |
| **Memory** | Rolling per-chat conversation log (capped at 40 rows/chat) in SQLite. Supplies context so the Orchestrator can resolve references like "mark *that* one done". | SQLite |
| **Reminder** | Builds the daily digest and overdue alerts. Transport-agnostic: it receives a `send(chat_id, text)` callable, so it never imports Telegram directly. | Task & Recommendation agents; any sender |

### Why this decomposition
Each agent has one reason to change. Swapping SQLite for Postgres touches only the Task and Memory agents' session layer; swapping Gemini for another LLM touches only `services/gemini_client.py`; adding WhatsApp means writing a new sender and reusing the Reminder Agent unchanged.

## 3. Data flow

**Conversational write path (Telegram or `/api/chat`):**

1. `TelegramService` receives an update and calls `orchestrator.handle_message(chat_id, text)`.
2. Orchestrator → **Memory Agent**: store the user turn, fetch the last 10 turns.
3. Orchestrator → **Gemini**: context + message → intent JSON (`create_task`, `complete_task`, …). If Gemini is unavailable or returns garbage, the regex fallback parser produces the intent instead — the system degrades, never breaks.
4. For `create_task`, Orchestrator → **Priority Agent** → priority value; then → **Task Agent** → INSERT via SQLAlchemy.
5. Orchestrator formats the reply, stores it in memory, returns it to the transport.

**Dashboard read path:** Browser JS → `GET /api/tasks?search=&status=&priority=&overdue=` → route → Task Agent → parameterised `SELECT` → Pydantic `TaskOut` list → rendered client-side.

**Reminder path:** APScheduler cron fires → Reminder Agent → Task Agent (stats, overdue) + Recommendation Agent (next-best task) → digest text → `telegram_service.send` per known chat.

## 4. Communication flow

- **User ↔ Telegram:** long polling (`Application.updater.start_polling`), started as an asyncio task inside FastAPI's lifespan. No public webhook needed, which simplifies Render deployment; switching to webhooks later only changes `TelegramService.start()`.
- **User ↔ Dashboard:** plain `fetch()` calls against the JSON API; the dashboard is stateless and could be hosted anywhere.
- **Agents ↔ Gemini:** one shared `GeminiClient` with `asyncio.to_thread` so the synchronous SDK never blocks the event loop; every call is wrapped, logged and allowed to fail to `None`.
- **Agents ↔ DB:** short-lived sessions via a `db_session()` context manager (commit/rollback handled centrally); FastAPI routes use the `get_db` dependency.

## 5. Scalability path

The system is intentionally a modular monolith — the cheapest thing that is correct — with clean seams for growth:

1. **Vertical first.** SQLite + a single Render instance comfortably handles personal/small-team load; WAL-mode SQLite sustains thousands of writes/min.
2. **Database:** `DATABASE_URL` is the only coupling. Point it at Postgres (Render managed) and add a driver; the SQLAlchemy 2.0 models are dialect-agnostic.
3. **Horizontal API scaling:** the REST/dashboard layer is stateless and can run N replicas behind Render's load balancer. The Telegram poller and scheduler must remain single-instance — split them into a Render **background worker** (same codebase, different start command) when replicating the web tier.
4. **Job durability:** move APScheduler to its SQLAlchemy job store, or replace with Celery/RQ + Redis, when reminders must survive multi-instance deployments.
5. **LLM throughput:** the Gemini client is a single chokepoint by design — add caching, rate limiting, retries or model routing there without touching any agent.
6. **Multi-tenancy:** `chat_id` is already stored per task; promoting it to a `User` table with auth is an additive migration.
