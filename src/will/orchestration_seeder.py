"""系统编排Seeder — 把真实系统编排关系注册为可视化workflow（P3-③ 编排树UI）

原则：数据必须真实，不虚构编排关系。三条编排链对应系统实际运行的消息/进化/器官协作流：
1. chat执行链：用户消息在系统中的真实处理路径（P0集成验证过的路径）
2. evo双链进化：strand_a/strand_b真实互审进化流程（dna_evolution.py实际流程）
3. 器官拓扑协作：OpenSoul器官层级真实调用关系（topology.py数据源一致）

幂等：同名workflow已存在则跳过，不重复创建。
"""

import logging

logger = logging.getLogger("opensoul.will.orchestration_seeder")

# 真实系统编排定义（名称→节点/边），节点config标注真实端口/文件，便于UI溯源
SYSTEM_ORCHESTRATIONS = [
    {
        "name": "chat执行链",
        "description": "用户消息真实处理路径：chat API压缩脱敏记忆→ACP proxy→hermes执行→轨迹记录（端口8090/8092）",
        "trigger": "manual",
        "nodes": [
            {
                "node_type": "trigger",
                "label": "用户消息",
                "config": {"source": "weixin/web", "endpoint": "POST /api/chat"},
                "position": {"x": 0, "y": 200},
            },
            {
                "node_type": "organ",
                "label": "OpenSoul chat(8090)",
                "config": {
                    "pipeline": [
                        "_compress_if_needed",
                        "_redact_outbound",
                        "_get_memory_context",
                        "_check_loop",
                    ],
                    "file": "src/api/chat.py",
                },
                "position": {"x": 220, "y": 200},
            },
            {
                "node_type": "agent",
                "label": "ACP proxy(8092)",
                "config": {
                    "file": "acp-proxy/proxy.py",
                    "queue": "interrupt_queue",
                    "timeout_s": 120,
                },
                "position": {"x": 440, "y": 200},
            },
            {
                "node_type": "agent",
                "label": "Hermes Agent",
                "config": {
                    "cmd": "hermes acp",
                    "pty": True,
                    "file": "acp-proxy/proxy.py ACPProcess",
                },
                "position": {"x": 660, "y": 200},
            },
            {
                "node_type": "organ",
                "label": "轨迹记录",
                "config": {"endpoint": "/api/trajectory", "file": "src/api/trajectory.py"},
                "position": {"x": 880, "y": 200},
            },
            {
                "node_type": "end",
                "label": "响应返回",
                "config": {"modes": ["SSE stream", "JSON"]},
                "position": {"x": 1100, "y": 200},
            },
        ],
        "edges": [
            ("用户消息", "OpenSoul chat(8090)", ""),
            ("OpenSoul chat(8090)", "ACP proxy(8092)", "降级: RAG不可用时8s超时走LLM-only"),
            ("ACP proxy(8092)", "Hermes Agent", "排队: 同session插话入interrupt_queue"),
            ("Hermes Agent", "轨迹记录", ""),
            ("轨迹记录", "响应返回", ""),
        ],
    },
    {
        "name": "evo双链进化",
        "description": "strand_a(conservative,8092)与strand_b(aggressive,8095)双链互审进化流水线（dna_evolution.py实际7阶段）",
        "trigger": "cron",
        "nodes": [
            {
                "node_type": "trigger",
                "label": "进化定时器",
                "config": {"interval_s": 3600, "strand": "dna_evolution.run"},
                "position": {"x": 0, "y": 250},
            },
            {
                "node_type": "action",
                "label": "观察+反思",
                "config": {
                    "stages": ["observe", "reflect"],
                    "noise_filter": "NOISE_OBS_TYPES(self_introspect/self_scan)",
                },
                "position": {"x": 200, "y": 250},
            },
            {
                "node_type": "condition",
                "label": "storm_check熔断",
                "config": {
                    "file": "failure_memory.py",
                    "ttl_s": 1800,
                    "rule": "窗口5次同签名→熔断，30min自动解锁",
                },
                "position": {"x": 400, "y": 250},
            },
            {
                "node_type": "parallel",
                "label": "双链并行",
                "config": {
                    "strands": ["strand_a:primary:conservative", "strand_b:shadow:aggressive"]
                },
                "position": {"x": 600, "y": 250},
            },
            {
                "node_type": "action",
                "label": "strand_a规划+实现",
                "config": {
                    "pipeline": ["_locate", "_plan(JSON鲁棒提取)", "_implement", "code_review"],
                    "file": "evolution_v2.py",
                },
                "position": {"x": 820, "y": 150},
            },
            {
                "node_type": "action",
                "label": "strand_b评审",
                "config": {
                    "role": "shadow交叉评审",
                    "heartbeat": "data/dna_heartbeat_strand_b.json",
                },
                "position": {"x": 820, "y": 350},
            },
            {
                "node_type": "condition",
                "label": "plan/code_review",
                "config": {
                    "judge": "LLM-as-Judge(P0-5)",
                    "verdicts": ["satisfied", "needs_revision", "failed"],
                },
                "position": {"x": 1040, "y": 250},
            },
            {
                "node_type": "merge",
                "label": "双链合议",
                "config": {"rule": "两链互审通过→滚动重启sync_partner"},
                "position": {"x": 1240, "y": 250},
            },
            {
                "node_type": "end",
                "label": "checkpoint+记忆沉淀",
                "config": {"files": ["dna_state_*.json", "dna_memory.json", "failure_memory.db"]},
                "position": {"x": 1440, "y": 250},
            },
        ],
        "edges": [
            ("进化定时器", "观察+反思", ""),
            ("观察+反思", "storm_check熔断", ""),
            ("storm_check熔断", "双链并行", "blocked_by_storm周期不喂熔断器"),
            ("双链并行", "strand_a规划+实现", ""),
            ("双链并行", "strand_b评审", ""),
            ("strand_a规划+实现", "plan/code_review", ""),
            ("strand_b评审", "plan/code_review", ""),
            ("plan/code_review", "双链合议", "failed→记failure_memory"),
            ("双链合议", "checkpoint+记忆沉淀", ""),
        ],
    },
    {
        "name": "器官拓扑协作",
        "description": "OpenSoul器官层级真实协作关系（与/api/topology/graph同源）",
        "trigger": "event",
        "nodes": [
            {
                "node_type": "trigger",
                "label": "Soul内核",
                "config": {"organ": "soul", "layer": "底层内核"},
                "position": {"x": 0, "y": 200},
            },
            {
                "node_type": "organ",
                "label": "Cortex推理",
                "config": {
                    "organ": "cortex",
                    "modules": ["loop_guard", "context_compression", "llm_retry"],
                },
                "position": {"x": 200, "y": 200},
            },
            {
                "node_type": "organ",
                "label": "Nerve总线",
                "config": {
                    "organ": "nerve",
                    "endpoint": "/api/nerve",
                    "events_example": ["soma.process_started"],
                },
                "position": {"x": 400, "y": 200},
            },
            {
                "node_type": "parallel",
                "label": "器官并行",
                "config": {},
                "position": {"x": 600, "y": 200},
            },
            {
                "node_type": "organ",
                "label": "Hippo记忆",
                "config": {
                    "organ": "hippo",
                    "modules": ["three_factor_retrieve", "session_importer(P3-②)"],
                },
                "position": {"x": 820, "y": 80},
            },
            {
                "node_type": "organ",
                "label": "Immune安全",
                "config": {
                    "organ": "immune",
                    "modules": ["permission_engine", "moderator", "rate_limiter"],
                },
                "position": {"x": 820, "y": 200},
            },
            {
                "node_type": "organ",
                "label": "Will工作流",
                "config": {
                    "organ": "will",
                    "modules": ["DAGPlanner", "job_queue(P0-8)", "WorkflowEngine"],
                },
                "position": {"x": 820, "y": 320},
            },
            {
                "node_type": "organ",
                "label": "Gene基因",
                "config": {
                    "organ": "gene",
                    "modules": ["skill_learner", ".agents/skills对齐(P3-①)"],
                },
                "position": {"x": 820, "y": 440},
            },
            {
                "node_type": "end",
                "label": "执行/输出",
                "config": {},
                "position": {"x": 1040, "y": 200},
            },
        ],
        "edges": [
            ("Soul内核", "Cortex推理", ""),
            ("Cortex推理", "Nerve总线", ""),
            ("Nerve总线", "器官并行", ""),
            ("器官并行", "Hippo记忆", ""),
            ("器官并行", "Immune安全", ""),
            ("器官并行", "Will工作流", ""),
            ("器官并行", "Gene基因", ""),
            ("Hippo记忆", "执行/输出", ""),
            ("Immune安全", "执行/输出", ""),
            ("Will工作流", "执行/输出", ""),
            ("Gene基因", "执行/输出", ""),
        ],
    },
]


def seed_system_orchestrations(engine) -> dict:
    """把真实系统编排注册到will workflow引擎（幂等）"""
    result = {"created": [], "skipped_existing": [], "errors": []}

    existing_names = {w.name for w in engine.list_workflows()}

    for spec in SYSTEM_ORCHESTRATIONS:
        if spec["name"] in existing_names:
            result["skipped_existing"].append(spec["name"])
            continue
        try:
            from src.will.models import NodeType, TriggerType

            wf = engine.create_workflow(
                name=spec["name"],
                description=spec["description"],
                trigger=TriggerType(spec["trigger"]),
            )
            label_to_id = {}
            for n in spec["nodes"]:
                # NodeType枚举无organ/agent——归入action，语义保留在config.kind
                ntype = n["node_type"]
                nconfig = dict(n["config"])
                if ntype in ("organ", "agent"):
                    nconfig["kind"] = ntype
                    ntype = "action"
                node = engine.add_node(
                    wf.id,
                    node_type=NodeType(ntype),
                    label=n["label"],
                    config=nconfig,
                    position=n["position"],
                )
                if node is None:
                    raise RuntimeError(f"add_node failed: {n['label']}")
                label_to_id[n["label"]] = node.id
            for src, dst, cond in spec["edges"]:
                engine.add_edge(
                    wf.id,
                    source_node_id=label_to_id[src],
                    target_node_id=label_to_id[dst],
                    condition=cond or None,
                )
            result["created"].append(
                {
                    "name": spec["name"],
                    "workflow_id": wf.id,
                    "nodes": len(spec["nodes"]),
                    "edges": len(spec["edges"]),
                }
            )
        except Exception as e:
            result["errors"].append({"name": spec["name"], "error": str(e)})
            logger.warning("seed %s failed: %s", spec["name"], e)

    return result
