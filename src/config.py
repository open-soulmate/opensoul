from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # PostgreSQL
    database_url: str = "postgresql+asyncpg://opensoul:opensoul@localhost:5432/opensoul"

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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
