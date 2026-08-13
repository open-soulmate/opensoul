import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from src.config import settings
from src.database.postgres import db_pool
from src.database.qdrant import qdrant_client
from src.database.meilisearch import meili_client

from src.api.knowledge import router as knowledge_router
from src.api.knowledge_requests import router as knowledge_requests_router
from src.api.kb_sharing import router as kb_sharing_router
from src.api.dedup import router as dedup_router
from src.api.permission import router as permission_router
from src.api.agent_proxy import router as agent_proxy_router
from src.a2a.api import router as a2a_router
from src.acp.api import router as acp_router
from src.api.hermes_bridge import router as hermes_bridge_router
from src.api.ws_chat import router as ws_router
from src.api.hermes_cron import router as hermes_cron_router
from src.api.agents import router as agents_router
from src.api.skills import router as skills_router
from src.api.marketplace import router as marketplace_router
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
    await db_pool.connect()
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
    await db_pool.disconnect()


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

# Static files
_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# Root route — serve admin dashboard
@app.get("/")
async def index():
    return FileResponse(os.path.join(_static_dir, "index.html"))


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
app.include_router(knowledge_requests_router, prefix="/api/knowledge-requests", tags=["knowledge-requests"])
app.include_router(kb_sharing_router, prefix="/api/kb-sharing", tags=["kb-sharing"])
app.include_router(dedup_router, prefix="/api/dedup", tags=["dedup"])
app.include_router(permission_router, prefix="/api/permission", tags=["permission"])
app.include_router(agent_proxy_router, prefix="/api/agent-proxy", tags=["agent-proxy"])
app.include_router(a2a_router, tags=["a2a"])
app.include_router(acp_router, prefix="/api/acp", tags=["acp"])
app.include_router(hermes_bridge_router, prefix="/api/hermes", tags=["hermes-bridge"])
app.include_router(ws_router, tags=["websocket"])
app.include_router(hermes_cron_router, prefix="/api/cron", tags=["cron"])
app.include_router(agents_router, prefix="/api/agents", tags=["agents"])
app.include_router(skills_router, prefix="/api/skills", tags=["skills"])
app.include_router(marketplace_router, prefix="/api/marketplace", tags=["marketplace"])
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
