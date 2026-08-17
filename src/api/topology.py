"""OpenTopology API — System topology graph with real-time health overlay."""
import time
import asyncio
import httpx
from fastapi import APIRouter

router = APIRouter()
_BASE = "http://127.0.0.1:8090"

# Component registry — same as registry but focused on topology
_TOPOLOGY_NODES = [
    {"id": "soul",       "name": "OpenSoul",       "category": "core",      "layer": "底层内核",     "emoji": "🧠", "endpoint": "/api/health"},
    {"id": "cortex",     "name": "OpenCortex",     "category": "core",      "layer": "底层扩展",     "emoji": "🧩", "endpoint": "/api/cortex/health"},
    {"id": "nerve",      "name": "OpenNerve",      "category": "core",      "layer": "中间总线",     "emoji": "⚡", "endpoint": "/api/nerve/health"},
    {"id": "vein",       "name": "OpenVein",       "category": "core",      "layer": "中间流转",     "emoji": "🩸", "endpoint": "/api/vein/health"},
    {"id": "soma",       "name": "OpenSoma",       "category": "core",      "layer": "中间采集层",   "emoji": "🤖", "endpoint": "/api/soma/health"},
    {"id": "sense",      "name": "OpenSense",      "category": "core",      "layer": "中间插件",     "emoji": "👁", "endpoint": "/api/sense/health"},
    {"id": "will",       "name": "OpenWill",       "category": "core",      "layer": "编排层",       "emoji": "✨", "endpoint": "/api/will/health"},
    {"id": "mate",       "name": "OpenMate",       "category": "core",      "layer": "上层用户端",   "emoji": "👤", "endpoint": "/api/health"},
    {"id": "immune",     "name": "OpenImmune",     "category": "service",   "layer": "安全底座",     "emoji": "🛡", "endpoint": "/api/immune/health"},
    {"id": "vital",      "name": "OpenVital",      "category": "service",   "layer": "运维监控",     "emoji": "📊", "endpoint": "/api/vital/health"},
    {"id": "marrow",     "name": "OpenMarrow",     "category": "service",   "layer": "灾备存储",     "emoji": "🦴", "endpoint": "/api/marrow/health"},
    {"id": "gland",      "name": "OpenGland",      "category": "service",   "layer": "模型网关",     "emoji": "🧪", "endpoint": "/api/gland/health"},
    {"id": "gene",       "name": "OpenGene",       "category": "service",   "layer": "模板生态",     "emoji": "🧬", "endpoint": "/api/gene/health"},
    {"id": "echo",       "name": "OpenEcho",       "category": "service",   "layer": "消息分发",     "emoji": "🔊", "endpoint": "/api/echo/health"},
    {"id": "mirror",     "name": "OpenMirror",     "category": "service",   "layer": "沙箱测试",     "emoji": "🪞", "endpoint": "/api/mirror/health"},
    {"id": "link",       "name": "OpenLink",       "category": "service",   "layer": "双向集成网关", "emoji": "🔗", "endpoint": "/api/link/health"},
    {"id": "hippo",      "name": "OpenHippo",      "category": "organ",     "layer": "记忆生命周期", "emoji": "🧠", "endpoint": "/api/hippo/health"},
    {"id": "reflex",     "name": "OpenReflex",     "category": "organ",     "layer": "高速应答引擎", "emoji": "⚡", "endpoint": "/api/reflex/health"},
    {"id": "heredity",   "name": "OpenHeredity",   "category": "organ",     "layer": "版本演化中心", "emoji": "🔗", "endpoint": "/api/heredity/health"},
    {"id": "nest",       "name": "OpenNest",       "category": "organ",     "layer": "多租户隔离",   "emoji": "🏠", "endpoint": "/api/nest/health"},
    {"id": "pulse",      "name": "OpenPulse",      "category": "organ",     "layer": "高精度时序",   "emoji": "💓", "endpoint": "/api/pulse/health"},
    {"id": "limb",       "name": "OpenLimb",       "category": "organ",     "layer": "RPA执行器",    "emoji": "💪", "endpoint": "/api/limb/health"},
    {"id": "voice",      "name": "OpenVoice",      "category": "organ",     "layer": "语音合成引擎", "emoji": "🎤", "endpoint": "/api/voice/health"},
    {"id": "vision",     "name": "OpenVision",     "category": "organ",     "layer": "图像可视化",   "emoji": "🎨", "endpoint": "/api/vision/health"},
    {"id": "mind",       "name": "OpenMind",       "category": "organ",     "layer": "人格调节层",   "emoji": "💭", "endpoint": "/api/mind/health"},
    {"id": "capture",    "name": "OpenCapture",    "category": "service",   "layer": "浏览器采集",   "emoji": "📸", "endpoint": "/api/capture/health"},
    {"id": "pipeline",   "name": "OpenPipeline",   "category": "service",   "layer": "跨组件流水线", "emoji": "🔀", "endpoint": "/api/pipeline/health"},
    {"id": "healer",     "name": "OpenHealer",     "category": "system",    "layer": "自愈系统",     "emoji": "💊", "endpoint": "/api/healer/health"},
    {"id": "intelligence","name": "OpenIntelligence","category": "system",  "layer": "智能分析",     "emoji": "🔍", "endpoint": "/api/intelligence/health"},
    {"id": "trajectory", "name": "OpenTrajectory", "category": "system",    "layer": "全链路追踪",   "emoji": "📈", "endpoint": "/api/trajectory/health"},
]

# Dependency edges (directed: from → depends on)
_EDGES = [
    ("soul", "cortex"), ("soul", "vein"), ("soul", "soma"), ("soul", "mate"),
    ("soul", "marrow"), ("soul", "hippo"), ("soul", "nest"), ("soul", "capture"),
    ("nerve", "vein"), ("nerve", "soma"), ("nerve", "immune"), ("nerve", "echo"),
    ("nerve", "link"), ("nerve", "limb"), ("nerve", "will"),
    ("gland", "sense"), ("gland", "will"), ("gland", "reflex"), ("gland", "mind"),
    ("vein", "pipeline"), ("sense", "pipeline"), ("immune", "pipeline"), ("soul", "pipeline"),
    ("healer", "nerve"), ("healer", "echo"), ("healer", "immune"),
]


@router.get("/health")
async def health():
    return {"status": "ok", "component": "OpenTopology", "nodes": len(_TOPOLOGY_NODES), "edges": len(_EDGES)}


@router.get("/stats")
async def topology_stats():
    """Get topology statistics."""
    categories = {}
    for node in _TOPOLOGY_NODES:
        cat = node["category"]
        categories[cat] = categories.get(cat, 0) + 1
    return {
        "status": "ok",
        "component": "OpenTopology",
        "total_nodes": len(_TOPOLOGY_NODES),
        "total_edges": len(_EDGES),
        "by_category": categories,
    }


@router.get("/graph")
async def get_topology_graph():
    """Return full topology graph with real-time health status."""
    # Check health of all components in parallel
    health_results = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        tasks = []
        for node in _TOPOLOGY_NODES:
            tasks.append(_check_node_health(client, node["id"], node["endpoint"]))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, dict):
                health_results[r["id"]] = r

    # Build response
    nodes = []
    for node in _TOPOLOGY_NODES:
        h = health_results.get(node["id"], {"status": "unknown", "response_time_ms": 0})
        nodes.append({
            "id": node["id"],
            "name": node["name"],
            "category": node["category"],
            "layer": node["layer"],
            "emoji": node["emoji"],
            "health": h.get("status", "unknown"),
            "response_time_ms": h.get("response_time_ms", 0),
        })

    edges = [{"from": f, "to": t} for f, t in _EDGES]

    # Stats
    healthy = sum(1 for n in nodes if n["health"] == "ok")
    unhealthy = sum(1 for n in nodes if n["health"] == "error")
    unknown = len(nodes) - healthy - unhealthy

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "total": len(nodes),
            "healthy": healthy,
            "unhealthy": unhealthy,
            "unknown": unknown,
            "total_edges": len(edges),
        },
        "timestamp": time.time(),
    }


async def _check_node_health(client: httpx.AsyncClient, node_id: str, endpoint: str):
    start = time.time()
    try:
        resp = await client.get(f"{_BASE}{endpoint}")
        elapsed = (time.time() - start) * 1000
        return {
            "id": node_id,
            "status": "ok" if resp.status_code == 200 else "error",
            "response_time_ms": round(elapsed, 1),
        }
    except Exception:
        elapsed = (time.time() - start) * 1000
        return {
            "id": node_id,
            "status": "error",
            "response_time_ms": round(elapsed, 1),
        }


@router.get("/clusters")
async def get_topology_clusters():
    """Return components grouped by category/layer."""
    clusters = {}
    for node in _TOPOLOGY_NODES:
        cat = node["category"]
        if cat not in clusters:
            clusters[cat] = []
        clusters[cat].append({
            "id": node["id"],
            "name": node["name"],
            "emoji": node["emoji"],
            "layer": node["layer"],
        })
    return {"clusters": clusters}


@router.get("/dependencies/{component_id}")
async def get_component_dependencies(component_id: str):
    """Get dependencies and dependents for a specific component."""
    depends_on = [t for f, t in _EDGES if f == component_id]
    depended_by = [f for f, t in _EDGES if t == component_id]
    return {
        "component": component_id,
        "depends_on": depends_on,
        "depended_by": depended_by,
        "direct_connections": len(depends_on) + len(depended_by),
    }
