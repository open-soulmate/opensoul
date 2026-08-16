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
from src.api.download_api import router as download_router
from src.api.terminal_ws import router as terminal_router
from src.api.ai_groups import router as ai_groups_router
from src.api.ai_engine import router as ai_engine_router
from src.api.agent_collaboration import router as agent_collab_router
from src.api.enterprise import router as enterprise_router
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
from src.api.cortex_enhanced import router as cortex_enhanced_router
from src.api.nerve import router as nerve_router
from src.api.will import router as will_router
from src.api.gland import router as gland_router
from src.api.gland import gateway as gland_gateway
from src.api.vital import router as vital_router
from src.api.vein import router as vein_router
from src.api.sense import router as sense_router
from src.api.immune import router as immune_router
from src.api.marrow import router as marrow_router
from src.api.gene import router as gene_router
from src.api.echo import router as echo_router
from src.api.mirror import router as mirror_router
from src.api.link import router as link_router
from src.api.hippo import router as hippo_router
from src.api.reflex import router as reflex_router
from src.api.heredity import router as heredity_router
from src.api.pulse import router as pulse_router
from src.api.nest import router as nest_router
from src.api.limb import router as limb_router
from src.api.voice import router as voice_router
from src.api.vision import router as vision_router
from src.api.mind import router as mind_router
from src.api.intelligence import router as intelligence_router
from src.api.trajectory import router as trajectory_router
from src.api.trajectory_api import router as trajectory_api_router
from src.api.plugins_api import router as plugins_router
from src.api.mcp import router as mcp_router
from src.api.learn import router as learn_router
from src.api.soma_connector import router as soma_connector_router
from src.api.config_api import router as config_api_router
from src.api.diagnostics import router as diagnostics_router
from src.api.admin_actions import router as admin_actions_router
from src.api.sessions_api import router as sessions_router
from src.api.event_stream import router as event_stream_router
from src.api.workspace_api import router as workspace_router
from src.api.git_api import router as git_router
from src.vital.collector import MetricsCollector
from src.vital.health import HealthChecker
from src.vital.alert import AlertManager
from src.plugin_loader import load_all_plugins


async def _intelligence_auto_collect():
    """Background task: periodically collect metrics from all organs for Intelligence analysis."""
    import time as _time
    import httpx as _httpx
    from src.api.intelligence import intelligence as _intel, _ORGAN_ENDPOINTS

    # Wait a bit for the server to fully start
    await asyncio.sleep(10)

    while True:
        try:
            base = "http://127.0.0.1:8090"
            async with _httpx.AsyncClient(timeout=5.0) as client:
                async def _collect(name: str, path: str):
                    start = _time.time()
                    try:
                        r = await client.get(f"{base}{path}")
                        elapsed_ms = (_time.time() - start) * 1000
                        data = r.json() if r.status_code == 200 else {}
                        _intel.record_metrics(name, {
                            "health": "ok" if r.status_code == 200 else "error",
                            "response_time_ms": elapsed_ms,
                            "custom": {k: v for k, v in data.items() if k != "status"},
                        })
                    except Exception:
                        elapsed_ms = (_time.time() - start) * 1000
                        _intel.record_metrics(name, {
                            "health": "error",
                            "response_time_ms": elapsed_ms,
                            "error_count": 1,
                        })
                await asyncio.gather(*[_collect(n, p) for n, p in _ORGAN_ENDPOINTS])
        except Exception:
            pass  # Never crash the background task

        await asyncio.sleep(120)  # Collect every 2 minutes


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

    # Intelligence auto-collect background task
    intel_task = asyncio.create_task(_intelligence_auto_collect())

    yield

    # Shutdown
    intel_task.cancel()
    try:
        await intel_task
    except asyncio.CancelledError:
        pass
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
app.mount("/admin", StaticFiles(directory=os.path.join(_static_dir, "admin")), name="admin")


# Root route — serve admin dashboard
@app.get("/")
async def index():
    return FileResponse(os.path.join(_static_dir, "index.html"))


import asyncio
import httpx

# Health check endpoint
@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Unified health — checks all organ endpoints in parallel
_ORGAN_HEALTH_ROUTES = [
    ("soul", "/api/health"),
    ("cortex", "/api/cortex/health"),
    ("cortex-enhanced", "/api/cortex/enhanced/health"),
    ("nerve", "/api/nerve/health"),
    ("vein", "/api/vein/health"),
    ("sense", "/api/sense/health"),
    ("will", "/api/will/health"),
    ("immune", "/api/immune/health"),
    ("vital", "/api/vital/health"),
    ("marrow", "/api/marrow/health"),
    ("gland", "/api/gland/health"),
    ("gene", "/api/gene/health"),
    ("echo", "/api/echo/health"),
    ("mirror", "/api/mirror/health"),
    ("link", "/api/link/health"),
    ("hippo", "/api/hippo/health"),
    ("reflex", "/api/reflex/health"),
    ("heredity", "/api/heredity/health"),
    ("pulse", "/api/pulse/health"),
    ("nest", "/api/nest/health"),
    ("limb", "/api/limb/health"),
    ("voice", "/api/voice/health"),
    ("vision", "/api/vision/health"),
    ("mind", "/api/mind/health"),
    ("intelligence", "/api/intelligence/health"),
    ("trajectory", "/api/trajectory/health"),
    ("mcp", "/api/mcp/health"),
    ("learn", "/api/learn/health"),
    ("diagnostics", "/api/diagnostics/health"),
    ("soma-connector", "/api/soma/health"),
    ("event-stream", "/api/events/health"),
]


@app.get("/api/health/all")
async def health_all():
    """Check all organ health endpoints and return aggregated status."""
    base = "http://127.0.0.1:8090"
    results = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        async def _check(name: str, path: str):
            try:
                r = await client.get(f"{base}{path}")
                results[name] = "ok" if r.status_code == 200 else "error"
            except Exception:
                results[name] = "error"
        await asyncio.gather(*[_check(n, p) for n, p in _ORGAN_HEALTH_ROUTES])
    ok = sum(1 for v in results.values() if v == "ok")
    return {"status": "ok" if ok == len(results) else "degraded", "healthy": ok, "total": len(results), "organs": results}

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
app.include_router(download_router, prefix="/api/download", tags=["download"])
app.include_router(terminal_router, tags=["terminal"])
app.include_router(ai_groups_router, tags=["ai-groups"])
app.include_router(ai_engine_router, tags=["ai-engine"])
app.include_router(agent_collab_router, prefix="/api/collab", tags=["agent-collaboration"])
app.include_router(enterprise_router, tags=["enterprise"])
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
app.include_router(cortex_enhanced_router, prefix="/api/cortex", tags=["cortex-enhanced"])
app.include_router(nerve_router, prefix="/api/nerve", tags=["nerve"])
app.include_router(will_router, prefix="/api/will", tags=["will"])
app.include_router(gland_router, prefix="/api/gland", tags=["gland"])
app.include_router(vital_router, prefix="/api/vital", tags=["vital"])
app.include_router(vein_router, prefix="/api/vein", tags=["vein"])
app.include_router(sense_router, prefix="/api/sense", tags=["sense"])
app.include_router(immune_router, prefix="/api/immune", tags=["immune"])
app.include_router(marrow_router, prefix="/api/marrow", tags=["marrow"])
app.include_router(gene_router, prefix="/api/gene", tags=["gene"])
app.include_router(echo_router, prefix="/api/echo", tags=["echo"])
app.include_router(mirror_router, prefix="/api/mirror", tags=["mirror"])
app.include_router(link_router, prefix="/api/link", tags=["link"])
app.include_router(hippo_router, prefix="/api/hippo", tags=["hippo"])
app.include_router(reflex_router, prefix="/api/reflex", tags=["reflex"])
app.include_router(heredity_router, prefix="/api/heredity", tags=["heredity"])
app.include_router(pulse_router, prefix="/api/pulse", tags=["pulse"])
app.include_router(nest_router, prefix="/api/nest", tags=["nest"])
app.include_router(limb_router, prefix="/api/limb", tags=["limb"])
app.include_router(voice_router, prefix="/api/voice", tags=["voice"])
app.include_router(vision_router, prefix="/api/vision", tags=["vision"])
app.include_router(mind_router, prefix="/api/mind", tags=["mind"])
app.include_router(intelligence_router, prefix="/api/intelligence", tags=["intelligence"])
app.include_router(trajectory_router, prefix="/api/trajectory", tags=["trajectory"])
app.include_router(trajectory_api_router, prefix="/api/trajectory-v2", tags=["trajectory-v2"])
app.include_router(plugins_router, prefix="/api/plugins", tags=["plugins"])
app.include_router(mcp_router, prefix="/api/mcp", tags=["mcp"])
app.include_router(learn_router, prefix="/api/learn", tags=["learn"])
app.include_router(soma_connector_router, prefix="/api/soma", tags=["soma-connector"])
app.include_router(config_api_router, prefix="/api", tags=["config"])
app.include_router(diagnostics_router, prefix="/api/diagnostics", tags=["diagnostics"])
app.include_router(admin_actions_router, prefix="/api/admin", tags=["admin"])
app.include_router(sessions_router, prefix="/api/sessions", tags=["sessions"])
app.include_router(event_stream_router, prefix="/api/events", tags=["event-stream"])
app.include_router(workspace_router, prefix="/api", tags=["workspace"])
app.include_router(git_router, prefix="/api/git", tags=["git"])

# Load and mount external plugins from ~/.openmate/plugins/
load_all_plugins(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host=settings.host, port=settings.port, reload=settings.debug)
