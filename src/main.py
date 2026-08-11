from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import settings
from src.database.postgres import pg_pool
from src.database.qdrant import qdrant_client
from src.database.meilisearch import meili_client

from src.api.knowledge import router as knowledge_router
from src.api.search import router as search_router
from src.api.chat import router as chat_router
from src.api.graph import router as graph_router
from src.api.entity import router as entity_router
from src.api.tag import router as tag_router
from src.api.user import router as user_router
from src.api.llm import router as llm_router
from src.api.agent import router as agent_router
from src.api.export import router as export_router
from src.api.cortex import router as cortex_router
from src.api.gland import router as gland_router
from src.api.gland import gateway as gland_gateway
from src.api.vital import router as vital_router
from src.vital.collector import MetricsCollector
from src.vital.health import HealthChecker
from src.vital.alert import AlertManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await pg_pool.connect()
    qdrant_client.ensure_collection()
    meili_client.ensure_index()
    await gland_gateway.startup()

    # Vital services
    collector = MetricsCollector()
    checker = HealthChecker()
    alert_mgr = AlertManager(collector)
    app.state.vital_collector = collector
    app.state.vital_checker = checker
    app.state.vital_alert_mgr = alert_mgr
    await collector.start()
    await alert_mgr.start()

    yield

    # Shutdown
    await alert_mgr.stop()
    await collector.stop()
    await gland_gateway.shutdown()
    await pg_pool.disconnect()


app = FastAPI(
    title="OpenSoul",
    description="Central Memory Kernel — REST API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware - allow all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health check endpoint
@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Version endpoint
@app.get("/api/version")
async def version():
    return {"version": app.version, "name": app.title}


# Register all API routers
app.include_router(knowledge_router, prefix="/api/knowledge", tags=["knowledge"])
app.include_router(search_router, prefix="/api/search", tags=["search"])
app.include_router(chat_router, prefix="/api/chat", tags=["chat"])
app.include_router(graph_router, prefix="/api/graph", tags=["graph"])
app.include_router(entity_router, prefix="/api/entity", tags=["entity"])
app.include_router(tag_router, prefix="/api/tags", tags=["tags"])
app.include_router(user_router, prefix="/api/user", tags=["user"])
app.include_router(llm_router, prefix="/api/llm", tags=["llm"])
app.include_router(agent_router, prefix="/api/agent", tags=["agent"])
app.include_router(export_router, prefix="/api/export", tags=["export"])
app.include_router(cortex_router, prefix="/api/cortex", tags=["cortex"])
app.include_router(gland_router, prefix="/api/gland", tags=["gland"])
app.include_router(vital_router, prefix="/api/vital", tags=["vital"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host=settings.host, port=settings.port, reload=settings.debug)
