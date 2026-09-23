from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database (sqlite:///path for dev, postgresql+asyncpg://... for prod)
    database_url: str = "sqlite:///data/opensoul.db"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "opensoul_knowledge"

    # Meilisearch
    meili_url: str = "http://localhost:7700"
    meili_key: str = "opensoul_master_key"
    meili_index: str = "opensoul_knowledge"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # LLM
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"

    # 记忆模型独立解析链（kilocode supplement3 #8 MemoryModel.port）：
    # 记忆蒸馏/整合用小模型省成本；无效/不可用→warn回退会话模型（llm_model）
    memory_model: str = ""  # 记忆蒸馏专用模型（空=回退会话模型）
    memory_base_url: str = ""  # 空=复用 llm_base_url
    memory_api_key: str = ""  # 空=复用 llm_api_key
    memory_llm_timeout_s: float = 120.0  # 端到端超时（timeout+调用方取消双闸）
    memory_temperature: float | None = None  # 采样按模型解析（None=调用方默认）
    memory_top_p: float | None = None
    memory_top_k: int | None = None

    # Embedding
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # NATS
    nats_url: str = ""

    # Alert
    alert_webhook_url: str = ""
    alert_email: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
