# Distributed AI Agent Orchestration System

A production-oriented distributed AI agent orchestration platform leveraging FastAPI, LangGraph, Google Gemini API, Redis, PostgreSQL, Celery, and OpenTelemetry.

---

## 🏗️ Architecture Overview

The system coordinates specialized autonomous AI agents capable of planning, research, code synthesis, evaluation, and distributed asynchronous execution.

```
                           +------------------------+
                           |  React Frontend / UI   |
                           +-----------+------------+
                                       |
                                       v
                           +------------------------+
                           |     FastAPI Server     |
                           |  (REST, SSE, WebSockets|
                           +-----------+------------+
                                       |
                                       v
                         +----------------------------+
                         |    LangGraph Orchestrator  |
                         | (State Graph, Multi-Agent) |
                         +-------------+--------------+
                                       |
                 +---------------------+---------------------+
                 |                                           |
                 v                                           v
    +--------------------------+               +--------------------------+
    |    Google Gemini API     |               |    Redis Message Broker  |
    | (gemini-2.5-flash / pro) |               +-------------+------------+
    +--------------------------+                             |
                                                             v
                                               +--------------------------+
                                               |      Celery Workers      |
                                               |  (Distributed Execution) |
                                               +-------------+------------+
                                                             |
                                                             v
                                               +--------------------------+
                                               |     PostgreSQL + Memory  |
                                               | (Persistence & Checkpts) |
                                               +--------------------------+
```

---

## 🛠️ Technology Stack

| Component | Technology |
|---|---|
| Runtime | Python 3.12 |
| API Layer | FastAPI + Uvicorn |
| Multi-Agent Orchestration | LangGraph + LangChain Core |
| LLM / Intelligence | Google Gemini API (`gemini-2.5-flash`, `gemini-2.5-pro`) |
| Data Validation | Pydantic v2 + Pydantic Settings |
| Persistence | PostgreSQL + SQLAlchemy (asyncpg) + Alembic |
| Broker & Cache | Redis |
| Distributed Execution | Celery |
| Observability | OpenTelemetry |
| Code Quality | Ruff (Linter & Formatter) |
| Test Suite | Pytest + Pytest-Asyncio |

---

## 🚀 Getting Started (Phase 1)

### 1. Prerequisites
- Python 3.12+
- `uv` (recommended) or standard `pip`

### 2. Environment Setup
```bash
# Create and activate Python 3.12 virtual environment
uv venv --python 3.12 .venv
.venv\Scripts\activate   # Windows

# Install dependencies
uv pip install -e ".[dev]"
```

### 3. Configure Environment Variables
```bash
cp .env.example .env
# Edit .env with your Google Gemini API key and credentials
```

### 4. Code Quality & Tests
```bash
# Run Ruff lint check
ruff check .

# Run Ruff formatter check
ruff format --check .

# Run Pytest suite
pytest
```

