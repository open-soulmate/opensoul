# OpenSoul

> **Central Memory Kernel** -- Give your AI Agents a persistent soul.

OpenSoul is a self-hosted memory kernel for AI Agents. It provides persistent knowledge storage, semantic search, knowledge graph, RAG chat, and real-time communication through a unified API. Connect any AI Agent via REST, WebSocket, or MCP protocol.

## Architecture

```
                        ┌──────────────────────────────────────┐
                        │           Client Layer               │
                        │                                      │
                        │  Web UI · CLI · AI Agent · MCP Client│
                        └──────┬───────┬───────┬───────┬───────┘
                               │       │       │       │
                        ┌──────▼───────▼───────▼───────▼───────┐
                        │            API Layer                  │
                        │                                      │
                        │  REST API    WebSocket    MCP Server  │
                        │  /api/*      /ws/{user}   (stdio)     │
                        │                                      │
                        │  ┌─────────────────────────────────┐  │
                        │  │  knowledge · search · chat ·    │  │
                        │  │  graph · entity · tag · user ·  │  │
                        │  │  llm · agent · export · cortex  │  │
                        │  └─────────────────────────────────┘  │
                        └──────┬───────────────┬───────────────┘
                               │               │
                        ┌──────▼───────────────▼───────────────┐
                        │          Service Layer                │
                        │                                      │
                        │  knowledge · extraction · chunking   │
                        │  embedding · entity · graph          │
                        │  search · rag · auth · cortex        │
                        └──┬─────┬──────┬──────┬──────────────┘
                           │     │      │      │
                    ┌──────▼┐ ┌─▼────┐ ┌▼────┐ ┌▼─────┐
                    │ Post- │ │Qdrant│ │Meili│ │Redis │
                    │ greSQL│ │      │ │searh│ │      │
                    │       │ │vector│ │full │ │cache │
                    │meta-  │ │seman-│ │text │ │sess- │
                    │data   │ │tic   │ │     │ │ion   │
                    └───────┘ └──────┘ └─────┘ └──────┘
```

## Tech Stack

| Component       | Technology              | Purpose                          |
|-----------------|-------------------------|----------------------------------|
| Web Framework   | FastAPI                 | REST API + WebSocket             |
| Database        | PostgreSQL + asyncpg    | Metadata, graph, users           |
| Vector Store    | Qdrant                  | Semantic search, embeddings      |
| Full-text Search| Meilisearch             | Keyword search, faceted search   |
| Cache           | Redis                   | Session, rate limiting           |
| Protocol        | MCP (stdio)             | AI Agent tool calling            |
| Auth            | JWT (HS256)             | Token-based authentication       |

## Quick Start

Get OpenSoul running in under 5 minutes.

### 1. Clone and configure

```bash
git clone https://github.com/your-org/opensoul.git
cd opensoul
cp .env.example .env
```

Edit `.env` and set your LLM API key:

```bash
LLM_API_KEY=sk-your-key-here
EMBEDDING_API_KEY=sk-your-key-here
```

### 2. Start everything

```bash
docker compose up -d
```

This starts PostgreSQL, Qdrant, Meilisearch, Redis, and the API server. Migrations run automatically.

### 3. Verify

```bash
curl http://localhost:8000/api/health
# → {"status":"ok"}
```

### 4. Register a user and get a token

```bash
# Register
curl -X POST http://localhost:8000/api/user/register \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","email":"alice@example.com","password":"secret123"}'

# Login
curl -X POST http://localhost:8000/api/user/login \
  -d "username=alice&password=secret123"
# → {"access_token":"eyJ...","token_type":"bearer"}
```

### 5. Store your first memory

```bash
TOKEN="eyJ..."  # paste your access_token

curl -X POST http://localhost:8000/api/knowledge/?user_id=<your-user-id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"First Memory","content":"OpenSoul remembers everything.","tags":["demo"]}'
```

### Available dashboards

| Service       | URL                              |
|---------------|----------------------------------|
| API           | http://localhost:8000             |
| Swagger UI    | http://localhost:8000/docs        |
| Qdrant        | http://localhost:6333/dashboard   |
| Meilisearch   | http://localhost:7700             |

## API Overview

Full documentation: [API.md](./API.md)

| Group    | Endpoints                          | Description                     |
|----------|------------------------------------|---------------------------------|
| Health   | `GET /api/health`, `GET /api/version` | Service status               |
| User     | `/api/user/*`                      | Register, login, profile        |
| Knowledge| `/api/knowledge/*`                 | CRUD for knowledge entries      |
| Search   | `/api/search/*`                    | Semantic, fulltext, hybrid      |
| Chat     | `POST /api/chat`                   | RAG chat with streaming         |
| Graph    | `/api/graph/*`                     | Knowledge graph queries         |
| Entity   | `/api/entity/*`                    | Entity CRUD                     |
| Tag      | `/api/tags/*`                      | Tag CRUD                        |
| LLM      | `/api/llm/*`                       | LLM config and proxy            |
| Agent    | `/api/agent/*`                     | Agent registration and memory   |
| Export   | `/api/export/*`                    | Data export (JSON, Markdown)    |
| Cortex   | `/api/cortex/*`                    | Planning, multi-agent, CoT      |
| WebSocket| `WS /ws/{user_id}`                 | Real-time events                |

### MCP Tools

Connect via MCP protocol for direct AI Agent integration:

| Tool          | Description                              |
|---------------|------------------------------------------|
| `remember`    | Store knowledge into long-term memory    |
| `recall`      | Semantic search for relevant memories    |
| `ask`         | RAG question answering                   |
| `search`      | Hybrid search (semantic + fulltext)      |
| `list_memories`| List all stored memories                |

## Environment Variables

All variables have sensible defaults. Copy `.env.example` to `.env` and customize.

### Database

| Variable            | Default                                          | Description         |
|---------------------|--------------------------------------------------|---------------------|
| `DATABASE_URL`      | `postgresql+asyncpg://opensoul:opensoul@localhost:5432/opensoul` | PostgreSQL connection string |
| `POSTGRES_USER`     | `opensoul`                                       | PostgreSQL username |
| `POSTGRES_PASSWORD` | `opensoul`                                       | PostgreSQL password |
| `POSTGRES_DB`       | `opensoul`                                       | Database name       |
| `POSTGRES_PORT`     | `5432`                                           | PostgreSQL port     |

### Qdrant

| Variable            | Default                  | Description           |
|---------------------|--------------------------|-----------------------|
| `QDRANT_URL`        | `http://localhost:6333`  | Qdrant server URL     |
| `QDRANT_PORT`       | `6333`                   | Qdrant port           |
| `QDRANT_COLLECTION` | `opensoul_knowledge`     | Default collection    |

### Meilisearch

| Variable            | Default                  | Description              |
|---------------------|--------------------------|--------------------------|
| `MEILI_URL`         | `http://localhost:7700`  | Meilisearch server URL   |
| `MEILI_PORT`        | `7700`                   | Meilisearch port         |
| `MEILI_MASTER_KEY`  | `opensoul_master_key`    | Meilisearch master key   |
| `MEILI_KEY`         | `opensoul_master_key`    | Key used by the API      |
| `MEILI_INDEX`       | `opensoul_knowledge`     | Default index name       |

### Redis

| Variable      | Default                       | Description       |
|---------------|-------------------------------|-------------------|
| `REDIS_URL`   | `redis://localhost:6379/0`    | Redis URL         |
| `REDIS_PORT`  | `6379`                        | Redis port        |

### Authentication

| Variable            | Default                  | Description                |
|---------------------|--------------------------|----------------------------|
| `JWT_SECRET`        | `change-me-in-production`| JWT signing secret          |
| `JWT_ALGORITHM`     | `HS256`                  | JWT algorithm              |
| `JWT_EXPIRE_MINUTES`| `1440`                   | Token expiry (24 hours)    |

### LLM

| Variable          | Default                        | Description            |
|-------------------|--------------------------------|------------------------|
| `LLM_API_KEY`     | *(empty)*                      | OpenAI-compatible API key |
| `LLM_BASE_URL`    | `https://api.openai.com/v1`    | LLM API base URL       |
| `LLM_MODEL`       | `gpt-4o`                       | Model name             |

### Embedding

| Variable              | Default                        | Description              |
|-----------------------|--------------------------------|--------------------------|
| `EMBEDDING_API_KEY`   | *(empty)*                      | Embedding API key        |
| `EMBEDDING_BASE_URL`  | `https://api.openai.com/v1`    | Embedding API base URL   |
| `EMBEDDING_MODEL`     | `text-embedding-3-small`       | Embedding model          |
| `EMBEDDING_DIMENSIONS`| `1536`                         | Vector dimensions        |

### Server

| Variable       | Default                               | Description          |
|----------------|---------------------------------------|----------------------|
| `HOST`         | `0.0.0.0`                             | Listen address       |
| `PORT`         | `8000`                                | Listen port          |
| `DEBUG`        | `true`                                | Enable debug mode    |
| `CORS_ORIGINS` | `["http://localhost:3000","http://localhost:5173"]` | Allowed CORS origins |

## Development

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Docker and Docker Compose

### Local setup

```bash
# Install dependencies
uv sync

# Install dev dependencies
uv sync --extra dev

# Start infrastructure only
docker compose up -d postgres qdrant meilisearch redis

# Run migrations (auto-runs via docker-entrypoint-initdb.d, or manual:)
psql -h localhost -U opensoul -d opensoul -f migrations/001_init.sql
psql -h localhost -U opensoul -d opensoul -f migrations/002_agents_and_star_pin.sql

# Start the API server with hot reload
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

### Run tests

```bash
uv run pytest
```

### Lint and type check

```bash
uv run ruff check .
uv run ruff format .
uv run pyright
```

### MCP Server (local)

```bash
python -m src.mcp.server
```

## Project Structure

```
opensoul/
├── src/
│   ├── main.py              # FastAPI application entry
│   ├── config.py            # Pydantic settings
│   ├── api/                 # API route handlers
│   │   ├── knowledge.py     # Knowledge CRUD
│   │   ├── search.py        # Search endpoints
│   │   ├── chat.py          # RAG chat (SSE streaming)
│   │   ├── graph.py         # Knowledge graph
│   │   ├── entity.py        # Entity CRUD
│   │   ├── tag.py           # Tag CRUD
│   │   ├── user.py          # Auth (register/login/me)
│   │   ├── llm.py           # LLM config and proxy
│   │   ├── agent.py         # Agent registration/memory
│   │   ├── export.py        # Data export
│   │   └── cortex.py        # Cortex (planning, CoT, multi-agent)
│   ├── models/              # Pydantic schemas
│   ├── services/            # Business logic
│   │   ├── knowledge.py     # Knowledge operations
│   │   ├── search.py        # Semantic + fulltext + hybrid
│   │   ├── rag.py           # RAG pipeline
│   │   ├── embedding.py     # Vector embedding
│   │   ├── chunking.py      # Text chunking
│   │   ├── extraction.py    # Content extraction
│   │   ├── entity.py        # Entity operations
│   │   ├── graph.py         # Graph queries
│   │   └── auth.py          # JWT auth + password hashing
│   ├── database/            # Database clients
│   │   ├── postgres.py      # asyncpg connection pool
│   │   ├── qdrant.py        # Qdrant client
│   │   └── meilisearch.py   # Meilisearch client
│   ├── middleware/           # FastAPI middleware
│   │   └── auth.py          # JWT + agent token middleware
│   ├── cortex/              # AI reasoning engine
│   │   ├── task_planner.py  # Goal → sub-tasks decomposition
│   │   ├── multi_agent.py   # Researcher → Analyzer → Writer
│   │   └── chain_of_thought.py  # Step-by-step reasoning
│   ├── mcp/                 # MCP Server (stdio)
│   │   └── server.py
│   └── websocket/           # WebSocket handler
│       └── handler.py
├── migrations/              # SQL schema migrations
│   ├── 001_init.sql
│   └── 002_agents_and_star_pin.sql
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── .env.example
```

## License

[MIT](./LICENSE)
