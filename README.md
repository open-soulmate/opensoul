# OpenSoul — Central Memory Kernel

OpenSoul 是一个中央记忆内核，为 AI Agent 提供持久化知识存储、语义搜索和实时通信能力。

## 三层架构

```
┌─────────────────────────────────────────────────────────┐
│                    Client Layer                          │
│   Web UI  │  CLI  │  AI Agent  │  MCP Client             │
└─────┬─────────┬──────────┬──────────┬───────────────────┘
      │         │          │          │
┌─────▼─────────▼──────────▼──────────▼───────────────────┐
│                   API Layer                              │
│  REST API  │  WebSocket  │  MCP Server (stdio)           │
│  /knowledge│  /ws        │  tools/*                      │
│  /search   │  realtime   │  resources/*                  │
│  /chat     │  events     │                               │
│  /graph    │             │                               │
└─────┬─────────────────────┬─────────────────────────────┘
      │                     │
┌─────▼─────────────────────▼─────────────────────────────┐
│               Service Layer                              │
│  knowledge │ extraction │ chunking │ embedding           │
│  entity    │ graph      │ search   │ rag                 │
│  auth      │                                    │        │
└─────┬───────────┬───────────┬───────────┬───────────────┘
      │           │           │           │
┌─────▼───┐ ┌────▼────┐ ┌───▼────┐ ┌───▼────┐
│PostgreSQL│ │ Qdrant  │ │Meili-  │ │ Redis  │
│ metadata │ │ vectors │ │search  │ │ cache  │
│ graph    │ │ semantic│ │fulltext│ │ session│
└──────────┘ └─────────┘ └────────┘ └────────┘
```

## 技术栈

| 组件 | 技术 | 用途 |
|------|------|------|
| Web 框架 | FastAPI | REST API + WebSocket |
| 关系数据库 | PostgreSQL + asyncpg | 元数据、图关系、用户 |
| 向量数据库 | Qdrant | 语义搜索、嵌入存储 |
| 全文搜索 | Meilisearch | 关键词搜索、faceted search |
| 缓存 | Redis | 会话、速率限制 |
| 协议 | MCP (stdio) | AI Agent 工具调用 |

## 快速启动

### 1. 环境准备

```bash
cp .env.example .env
# 编辑 .env 填入你的 LLM API Key
```

### 2. Docker Compose（推荐）

```bash
docker compose up -d
```

服务启动后：
- API: http://localhost:8000
- API 文档: http://localhost:8000/docs
- Qdrant Dashboard: http://localhost:6333/dashboard
- Meilisearch Dashboard: http://localhost:7700

### 3. 本地开发

```bash
# 安装依赖
uv sync

# 启动基础设施
docker compose up -d postgres qdrant meilisearch redis

# 运行数据库迁移
psql -h localhost -U opensoul -d opensoul -f migrations/001_init.sql

# 启动服务
uvicorn src.main:app --reload
```

### 4. MCP Server

在你的 AI Agent 配置中添加：

```json
{
  "mcpServers": {
    "opensoul": {
      "command": "python",
      "args": ["-m", "src.mcp.server"],
      "env": {
        "DATABASE_URL": "postgresql+asyncpg://opensoul:opensoul@localhost:5432/opensoul"
      }
    }
  }
}
```

## API 概览

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/knowledge` | CRUD | 知识条目管理 |
| `/api/v1/search` | POST | 混合搜索（语义+全文） |
| `/api/v1/chat` | POST | RAG 对话 |
| `/api/v1/graph` | GET | 知识图谱查询 |
| `/api/v1/entities` | CRUD | 实体管理 |
| `/api/v1/tags` | CRUD | 标签管理 |
| `/api/v1/users` | CRUD | 用户管理 |
| `/api/v1/llm` | POST | LLM 代理 |
| `/api/v1/agent` | POST | Agent 交互 |
| `/api/v1/export` | GET | 数据导出 |
| `/ws` | WS | 实时事件推送 |

## 项目结构

```
opensoul/
├── src/
│   ├── main.py           # FastAPI 入口
│   ├── config.py         # 配置管理
│   ├── database/         # 数据库连接
│   │   ├── postgres.py   # asyncpg 连接池
│   │   ├── qdrant.py     # Qdrant 客户端
│   │   └── meilisearch.py# Meilisearch 客户端
│   ├── models/           # Pydantic 模型
│   │   ├── knowledge.py
│   │   ├── entity.py
│   │   ├── user.py
│   │   └── tag.py
│   ├── services/         # 业务逻辑
│   │   ├── knowledge.py
│   │   ├── extraction.py
│   │   ├── chunking.py
│   │   ├── embedding.py
│   │   ├── entity.py
│   │   ├── graph.py
│   │   ├── search.py
│   │   ├── rag.py
│   │   └── auth.py
│   ├── api/              # API 路由
│   ├── mcp/              # MCP Server
│   └── websocket/        # WebSocket 处理
├── migrations/           # SQL 迁移
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## License

MIT
