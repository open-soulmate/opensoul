"""Component Registry API — unified metadata for all 25 components.

Enables plug-and-play discovery: any component or external tool can query
this endpoint to learn what's available, what depends on what, and how
to interact with each component.

This is the key enabler for the "一切皆插件" architecture.
"""
import time
from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()

# ── Component Manifests ──────────────────────────────────────────
# Each component declares: name, emoji, category, layer, description,
# api_prefix, health_endpoint, capabilities, dependencies, version.

COMPONENT_MANIFESTS = [
    {
        "id": "soul",
        "name": "OpenSoul",
        "emoji": "🧠",
        "category": "core",
        "layer": "底层内核",
        "description": "中央记忆内核、文档解析、RAG多路召回、知识图谱、RBAC权限审计",
        "api_prefix": "/api",
        "health_endpoint": "/api/health",
        "capabilities": ["knowledge", "rag", "graph", "rbac", "mcp", "search"],
        "dependencies": [],
        "version": "0.1.0",
        "phase": "一期",
    },
    {
        "id": "cortex",
        "name": "OpenCortex",
        "emoji": "🧩",
        "category": "core",
        "layer": "底层扩展",
        "description": "高级认知、长周期任务规划、CoT思维链、多Agent协作推理、GraphRAG",
        "api_prefix": "/api/cortex",
        "health_endpoint": "/api/cortex/health",
        "capabilities": ["planning", "cot", "multi-agent", "graphrag", "recommendations"],
        "dependencies": ["soul"],
        "version": "0.1.0",
        "phase": "二期",
    },
    {
        "id": "nerve",
        "name": "OpenNerve",
        "emoji": "⚡",
        "category": "core",
        "layer": "中间总线",
        "description": "事件总线、WebSocket长连接、分布式节点消息分发",
        "api_prefix": "/api/nerve",
        "health_endpoint": "/api/nerve/health",
        "capabilities": ["event-bus", "websocket", "pub-sub", "message-routing"],
        "dependencies": [],
        "version": "0.1.0",
        "phase": "二期",
    },
    {
        "id": "vein",
        "name": "OpenVein",
        "emoji": "🩸",
        "category": "core",
        "layer": "中间流转",
        "description": "大文件分片上传、缓存管理、内容去重、文件版本控制",
        "api_prefix": "/api/vein",
        "health_endpoint": "/api/vein/health",
        "capabilities": ["chunked-upload", "cache", "dedup", "versioning", "file-store"],
        "dependencies": ["nerve"],
        "version": "0.1.0",
        "phase": "二期",
    },
    {
        "id": "soma",
        "name": "OpenSoma",
        "emoji": "🤖",
        "category": "core",
        "layer": "中间采集层",
        "description": "分布式采集Agent、第三方连接器、多源数据采集+基础清洗（只读采集）",
        "api_prefix": "/api/soma",
        "health_endpoint": "/api/health",
        "capabilities": ["collect", "connectors", "preprocessing", "rss", "feishu", "dingtalk"],
        "dependencies": ["soul", "nerve"],
        "version": "0.1.0",
        "phase": "一期",
    },
    {
        "id": "sense",
        "name": "OpenSense",
        "emoji": "👁",
        "category": "core",
        "layer": "中间插件",
        "description": "多模态解析：OCR图像识别、ASR语音转写、视频抽帧解析",
        "api_prefix": "/api/sense",
        "health_endpoint": "/api/sense/health",
        "capabilities": ["ocr", "asr", "multimodal", "video-frames", "llm-fallback"],
        "dependencies": ["gland"],
        "version": "0.1.0",
        "phase": "二期",
    },
    {
        "id": "will",
        "name": "OpenWill",
        "emoji": "✨",
        "category": "core",
        "layer": "编排层",
        "description": "复杂工作流编排、条件触发、多分支定时业务流程",
        "api_prefix": "/api/will",
        "health_endpoint": "/api/will/health",
        "capabilities": ["workflow", "cron", "conditions", "cross-component-exec"],
        "dependencies": ["nerve", "gland"],
        "version": "0.1.0",
        "phase": "二期",
    },
    {
        "id": "mate",
        "name": "OpenMate",
        "emoji": "👤",
        "category": "core",
        "layer": "上层用户端",
        "description": "多端交互入口：Web、Tauri桌面、浏览器插件、MCP客户端",
        "api_prefix": "/",
        "health_endpoint": "/",
        "capabilities": ["web-ui", "tauri", "browser-extension", "mcp-client", "i18n"],
        "dependencies": ["soul"],
        "version": "0.1.0",
        "phase": "一期",
    },
    {
        "id": "immune",
        "name": "OpenImmune",
        "emoji": "🛡",
        "category": "platform",
        "layer": "安全底座",
        "description": "内容风控、敏感数据脱敏、访问限流、IP管控、安全审计",
        "api_prefix": "/api/immune",
        "health_endpoint": "/api/immune/health",
        "capabilities": ["content-moderation", "rate-limit", "ip-control", "audit", "pii-detection"],
        "dependencies": ["nerve"],
        "version": "0.1.0",
        "phase": "三期",
    },
    {
        "id": "vital",
        "name": "OpenVital",
        "emoji": "📊",
        "category": "platform",
        "layer": "运维监控",
        "description": "全平台指标采集、节点健康状态、性能监控、告警通知",
        "api_prefix": "/api/vital",
        "health_endpoint": "/api/vital/health",
        "capabilities": ["metrics", "health-check", "alerts", "dashboards"],
        "dependencies": [],
        "version": "0.1.0",
        "phase": "二期",
    },
    {
        "id": "marrow",
        "name": "OpenMarrow",
        "emoji": "🦴",
        "category": "platform",
        "layer": "灾备存储",
        "description": "知识库快照、定时备份、灾备恢复、跨环境数据迁移",
        "api_prefix": "/api/marrow",
        "health_endpoint": "/api/marrow/health",
        "capabilities": ["backup", "restore", "scheduled-backup", "migration", "snapshot"],
        "dependencies": ["soul"],
        "version": "0.1.0",
        "phase": "三期",
    },
    {
        "id": "gland",
        "name": "OpenGland",
        "emoji": "🧪",
        "category": "platform",
        "layer": "模型网关",
        "description": "多LLM统一调度、模型池路由、密钥管理、Token计量、负载均衡",
        "api_prefix": "/api/gland",
        "health_endpoint": "/api/gland/health",
        "capabilities": ["llm-routing", "model-pool", "token-metering", "load-balance", "fallback"],
        "dependencies": [],
        "version": "0.1.0",
        "phase": "二期",
    },
    {
        "id": "gene",
        "name": "OpenGene",
        "emoji": "🧬",
        "category": "platform",
        "layer": "模板生态",
        "description": "行业预制模板库、Agent配方、知识库模板、工作流一键配置模板",
        "api_prefix": "/api/gene",
        "health_endpoint": "/api/gene/health",
        "capabilities": ["templates", "agent-recipes", "import-export", "clone", "categories"],
        "dependencies": [],
        "version": "0.1.0",
        "phase": "三期",
    },
    {
        "id": "echo",
        "name": "OpenEcho",
        "emoji": "🔊",
        "category": "platform",
        "layer": "消息分发",
        "description": "多渠道消息推送：钉钉/企微/邮件/SMS/Webhook结果通知",
        "api_prefix": "/api/echo",
        "health_endpoint": "/api/echo/health",
        "capabilities": ["webhook", "dingtalk", "wechat-work", "email", "telegram", "feishu"],
        "dependencies": ["nerve"],
        "version": "0.1.0",
        "phase": "三期",
    },
    {
        "id": "mirror",
        "name": "OpenMirror",
        "emoji": "🪞",
        "category": "platform",
        "layer": "沙箱测试",
        "description": "隔离测试沙箱，调试工作流/连接器/Agent，不污染正式知识库",
        "api_prefix": "/api/mirror",
        "health_endpoint": "/api/mirror/health",
        "capabilities": ["sandbox", "isolation", "testing", "ttl", "snapshot"],
        "dependencies": [],
        "version": "0.1.0",
        "phase": "三期",
    },
    {
        "id": "link",
        "name": "OpenLink",
        "emoji": "🔗",
        "category": "platform",
        "layer": "双向集成网关",
        "description": "外部系统双向Webhook、OA/ERP低代码对接，外部可下发指令给平台",
        "api_prefix": "/api/link",
        "health_endpoint": "/api/link/health",
        "capabilities": ["webhook-in", "webhook-out", "rest-api", "oa-system", "custom"],
        "dependencies": ["nerve"],
        "version": "0.1.0",
        "phase": "三期",
    },
    {
        "id": "hippo",
        "name": "OpenHippo",
        "emoji": "🧠",
        "category": "advanced",
        "layer": "记忆生命周期",
        "description": "短期记忆管理、记忆自动归档、遗忘衰减策略、会话生命周期管理",
        "api_prefix": "/api/hippo",
        "health_endpoint": "/api/hippo/health",
        "capabilities": ["memory-decay", "session-mgmt", "archival", "forgetting-curve"],
        "dependencies": ["soul"],
        "version": "0.1.0",
        "phase": "四期",
    },
    {
        "id": "reflex",
        "name": "OpenReflex",
        "emoji": "⚡",
        "category": "advanced",
        "layer": "高速应答引擎",
        "description": "高频问题缓存、短路径快速反射响应，减少大模型重复推理",
        "api_prefix": "/api/reflex",
        "health_endpoint": "/api/reflex/health",
        "capabilities": ["response-cache", "fast-path", "importance-scoring", "eviction"],
        "dependencies": ["gland"],
        "version": "0.1.0",
        "phase": "四期",
    },
    {
        "id": "heredity",
        "name": "OpenHeredity",
        "emoji": "🔗",
        "category": "advanced",
        "layer": "版本演化中心",
        "description": "平台配置演化、插件版本管理、平滑升级、知识库结构迁移兼容",
        "api_prefix": "/api/heredity",
        "health_endpoint": "/api/heredity/health",
        "capabilities": ["version-registry", "migration", "rollback", "compatibility-check"],
        "dependencies": [],
        "version": "0.1.0",
        "phase": "四期",
    },
    {
        "id": "nest",
        "name": "OpenNest",
        "emoji": "🏠",
        "category": "advanced",
        "layer": "多租户隔离",
        "description": "租户资源配额、向量空间逻辑隔离、SaaS多租户资源池管控",
        "api_prefix": "/api/nest",
        "health_endpoint": "/api/nest/health",
        "capabilities": ["tenants", "quotas", "isolation", "resource-tracking", "tiers"],
        "dependencies": ["soul"],
        "version": "0.1.0",
        "phase": "四期",
    },
    {
        "id": "pulse",
        "name": "OpenPulse",
        "emoji": "💓",
        "category": "advanced",
        "layer": "高精度时序节拍器",
        "description": "底层高频时钟信号、亚秒级周期性轮询；只输出时间节拍，无业务逻辑",
        "api_prefix": "/api/pulse",
        "health_endpoint": "/api/pulse/health",
        "capabilities": ["timer", "signals", "periodic-tick", "callback"],
        "dependencies": [],
        "version": "0.1.0",
        "phase": "四期",
    },
    {
        "id": "limb",
        "name": "OpenLimb",
        "emoji": "💪",
        "category": "advanced",
        "layer": "RPA执行器",
        "description": "浏览器自动化、网页模拟点击、表单填报、外部系统写入操作",
        "api_prefix": "/api/limb",
        "health_endpoint": "/api/limb/health",
        "capabilities": ["rpa", "browser-auto", "form-fill", "task-queue", "templates"],
        "dependencies": ["nerve"],
        "version": "0.1.0",
        "phase": "四期",
    },
    {
        "id": "voice",
        "name": "OpenVoice",
        "emoji": "🎤",
        "category": "advanced",
        "layer": "语音合成引擎",
        "description": "TTS文字转语音、知识库有声朗读、语音播报输出",
        "api_prefix": "/api/voice",
        "health_endpoint": "/api/voice/health",
        "capabilities": ["tts", "voice-profiles", "edge-tts", "audio-stream"],
        "dependencies": [],
        "version": "0.1.0",
        "phase": "四期",
    },
    {
        "id": "vision",
        "name": "OpenVision",
        "emoji": "🎨",
        "category": "advanced",
        "layer": "视觉成像中枢",
        "description": "根据知识内容生成图表、思维导图、示意图",
        "api_prefix": "/api/vision",
        "health_endpoint": "/api/vision/health",
        "capabilities": ["bar-chart", "line-chart", "pie-chart", "scatter", "mindmap"],
        "dependencies": [],
        "version": "0.1.0",
        "phase": "四期",
    },
    {
        "id": "mind",
        "name": "OpenMind",
        "emoji": "💭",
        "category": "advanced",
        "layer": "人格调节层",
        "description": "用户情绪识别、对话人格库、个性化语气、角色风格动态调整",
        "api_prefix": "/api/mind",
        "health_endpoint": "/api/mind/health",
        "capabilities": ["emotion-analysis", "personality", "tone-adjustment", "style-switch"],
        "dependencies": ["gland"],
        "version": "0.1.0",
        "phase": "四期",
    },
]


# ── Dependency Graph ─────────────────────────────────────────────

def _build_dependency_graph() -> dict:
    """Build a directed dependency graph."""
    graph: dict[str, list[str]] = {}
    for comp in COMPONENT_MANIFESTS:
        graph[comp["id"]] = comp.get("dependencies", [])
    return graph


def _build_reverse_deps() -> dict[str, list[str]]:
    """Build reverse dependency map: component → who depends on it."""
    rev: dict[str, list[str]] = {c["id"]: [] for c in COMPONENT_MANIFESTS}
    for comp in COMPONENT_MANIFESTS:
        for dep in comp.get("dependencies", []):
            if dep in rev:
                rev[dep].append(comp["id"])
    return rev


def _topological_layers() -> list[list[str]]:
    """Group components into dependency layers (BFS)."""
    graph = _build_dependency_graph()
    visited: set[str] = set()
    layers: list[list[str]] = []

    while len(visited) < len(graph):
        # Find nodes whose deps are all visited
        layer = []
        for node, deps in graph.items():
            if node in visited:
                continue
            if all(d in visited for d in deps):
                layer.append(node)
        if not layer:
            # Circular or missing deps — add remaining
            layer = [n for n in graph if n not in visited]
        layers.append(sorted(layer))
        visited.update(layer)

    return layers


# ── API Endpoints ────────────────────────────────────────────────

@router.get("/components")
async def list_components(
    category: str | None = Query(default=None, description="Filter: core, platform, advanced"),
    phase: str | None = Query(default=None, description="Filter: 一期, 二期, 三期, 四期"),
):
    """List all registered components with full metadata."""
    results = COMPONENT_MANIFESTS
    if category:
        results = [c for c in results if c["category"] == category]
    if phase:
        results = [c for c in results if c["phase"] == phase]
    return {
        "total": len(results),
        "components": results,
    }


@router.get("/components/{component_id}")
async def get_component(component_id: str):
    """Get detailed metadata for a single component."""
    for comp in COMPONENT_MANIFESTS:
        if comp["id"] == component_id:
            rev = _build_reverse_deps()
            return {
                **comp,
                "dependents": rev.get(component_id, []),
            }
    from fastapi import HTTPException
    raise HTTPException(404, f"Component '{component_id}' not found")


@router.get("/components/{component_id}/dependencies")
async def get_dependencies(component_id: str):
    """Get dependency tree for a component (what it needs + what needs it)."""
    comp = None
    for c in COMPONENT_MANIFESTS:
        if c["id"] == component_id:
            comp = c
            break
    if not comp:
        from fastapi import HTTPException
        raise HTTPException(404, f"Component '{component_id}' not found")

    rev = _build_reverse_deps()
    graph = _build_dependency_graph()

    # Transitive dependencies
    def _transitive_deps(node: str, visited: set[str] | None = None) -> set[str]:
        if visited is None:
            visited = set()
        for dep in graph.get(node, []):
            if dep not in visited:
                visited.add(dep)
                _transitive_deps(dep, visited)
        return visited

    return {
        "component_id": component_id,
        "direct_dependencies": graph.get(component_id, []),
        "transitive_dependencies": sorted(_transitive_deps(component_id)),
        "dependents": rev.get(component_id, []),
    }


@router.get("/graph")
async def get_dependency_graph():
    """Get the full dependency graph for visualization."""
    graph = _build_dependency_graph()
    rev = _build_reverse_deps()
    layers = _topological_layers()

    nodes = []
    for comp in COMPONENT_MANIFESTS:
        nodes.append({
            "id": comp["id"],
            "name": comp["name"],
            "emoji": comp["emoji"],
            "category": comp["category"],
            "layer": comp["layer"],
            "phase": comp["phase"],
        })

    edges = []
    for comp in COMPONENT_MANIFESTS:
        for dep in comp.get("dependencies", []):
            edges.append({"from": dep, "to": comp["id"]})

    return {
        "nodes": nodes,
        "edges": edges,
        "layers": layers,
        "stats": {
            "total_components": len(COMPONENT_MANIFESTS),
            "total_dependencies": len(edges),
            "categories": {
                "core": len([c for c in COMPONENT_MANIFESTS if c["category"] == "core"]),
                "platform": len([c for c in COMPONENT_MANIFESTS if c["category"] == "platform"]),
                "advanced": len([c for c in COMPONENT_MANIFESTS if c["category"] == "advanced"]),
            },
            "phases": {
                "一期": len([c for c in COMPONENT_MANIFESTS if c["phase"] == "一期"]),
                "二期": len([c for c in COMPONENT_MANIFESTS if c["phase"] == "二期"]),
                "三期": len([c for c in COMPONENT_MANIFESTS if c["phase"] == "三期"]),
                "四期": len([c for c in COMPONENT_MANIFESTS if c["phase"] == "四期"]),
            },
        },
    }


@router.get("/capabilities")
async def list_capabilities():
    """List all capabilities across components (for capability-based discovery)."""
    cap_map: dict[str, list[str]] = {}
    for comp in COMPONENT_MANIFESTS:
        for cap in comp.get("capabilities", []):
            cap_map.setdefault(cap, []).append(comp["id"])

    return {
        "total_capabilities": len(cap_map),
        "capabilities": {k: sorted(v) for k, v in sorted(cap_map.items())},
    }


@router.get("/search")
async def search_components(
    q: str = Query(description="Search query (matches name, description, capabilities)"),
):
    """Search components by keyword."""
    q_lower = q.lower()
    results = []
    for comp in COMPONENT_MANIFESTS:
        score = 0
        if q_lower in comp["name"].lower():
            score += 3
        if q_lower in comp["description"].lower():
            score += 2
        if any(q_lower in cap.lower() for cap in comp.get("capabilities", [])):
            score += 1
        if q_lower in comp["id"].lower():
            score += 2
        if score > 0:
            results.append({**comp, "relevance_score": score})

    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    return {"query": q, "results": results}
