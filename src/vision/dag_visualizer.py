"""DAG visualizer for OpenVision.

Renders execution plans as visual DAG diagrams.
Borrowed from: React Flow, Airflow DAG, Mermaid
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("opensoul.vision.dag")


@dataclass
class DAGNode:
    node_id: str
    label: str
    node_type: str = "task"
    status: str = "pending"
    metadata: dict = field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0


@dataclass
class DAGEdge:
    source: str
    target: str
    label: str = ""
    edge_type: str = "normal"


@dataclass
class DAGLayout:
    nodes: list[DAGNode] = field(default_factory=list)
    edges: list[DAGEdge] = field(default_factory=list)
    title: str = ""
    description: str = ""


class DAGVisualizer:
    """Creates visual DAG layouts from execution plans."""

    def __init__(self):
        self._layouts: dict[str, DAGLayout] = {}

    def create_from_plan(
        self,
        plan_id: str,
        goal: str,
        steps: list[dict],
    ) -> DAGLayout:
        """Create a DAG layout from plan steps."""
        nodes = []
        edges = []

        for i, step in enumerate(steps):
            node = DAGNode(
                node_id=step.get("id", f"step_{i}"),
                label=step.get("description", f"Step {i}")[:40],
                node_type="task",
                status=step.get("status", "pending"),
                x=float(i % 4) * 200,
                y=float(i // 4) * 100,
            )
            nodes.append(node)

        for i, step in enumerate(steps):
            for dep in step.get("depends_on", []):
                edges.append(DAGEdge(source=dep, target=step.get("id", f"step_{i}")))

        # Linear chain if no dependencies
        if not edges and len(steps) > 1:
            for i in range(len(steps) - 1):
                edges.append(DAGEdge(
                    source=steps[i].get("id", f"step_{i}"),
                    target=steps[i + 1].get("id", f"step_{i+1}"),
                ))

        layout = DAGLayout(
            nodes=nodes,
            edges=edges,
            title=goal[:60],
            description=f"执行计划: {len(nodes)}个步骤, {len(edges)}个依赖",
        )
        self._layouts[plan_id] = layout
        return layout

    def to_mermaid(self, plan_id: str) -> str:
        """Export as Mermaid diagram."""
        layout = self._layouts.get(plan_id)
        if not layout:
            return "graph TD\n    empty[无数据]"

        lines = ["graph TD"]
        status_icons = {
            "pending": "⏳", "running": "🔄",
            "completed": "✅", "failed": "❌",
        }

        for node in layout.nodes:
            icon = status_icons.get(node.status, "")
            safe = node.label.replace('"', "'")
            lines.append(f'    {node.node_id}["{icon} {safe}"]')

        for edge in layout.edges:
            lines.append(f"    {edge.source} --> {edge.target}")

        return "\n".join(lines)

    def to_react_flow(self, plan_id: str) -> dict:
        """Export as React Flow JSON."""
        layout = self._layouts.get(plan_id)
        if not layout:
            return {"nodes": [], "edges": []}

        rf_nodes = [
            {
                "id": n.node_id,
                "type": "custom",
                "position": {"x": n.x, "y": n.y},
                "data": {"label": n.label, "status": n.status, "nodeType": n.node_type},
            }
            for n in layout.nodes
        ]
        rf_edges = [
            {"id": f"edge_{i}", "source": e.source, "target": e.target, "label": e.label}
            for i, e in enumerate(layout.edges)
        ]
        return {"nodes": rf_nodes, "edges": rf_edges}

    def to_ascii(self, plan_id: str) -> str:
        """Export as ASCII tree."""
        layout = self._layouts.get(plan_id)
        if not layout:
            return "(empty)"

        lines = [f"📊 {layout.title}", ""]
        children: dict[str, list[str]] = {}
        for edge in layout.edges:
            children.setdefault(edge.source, []).append(edge.target)

        all_targets = {e.target for e in layout.edges}
        roots = [n.node_id for n in layout.nodes if n.node_id not in all_targets]
        visited: set[str] = set()
        status_icons = {
            "pending": "⏳", "running": "🔄",
            "completed": "✅", "failed": "❌",
        }

        def dfs(node_id: str, prefix: str = "", is_last: bool = True):
            if node_id in visited:
                return
            visited.add(node_id)
            connector = "└── " if is_last else "├── "
            node = next((n for n in layout.nodes if n.node_id == node_id), None)
            if node:
                icon = status_icons.get(node.status, "")
                lines.append(f"{prefix}{connector}{icon} {node.label[:50]}")
            child_prefix = prefix + ("    " if is_last else "│   ")
            kids = children.get(node_id, [])
            for i, kid in enumerate(kids):
                dfs(kid, child_prefix, i == len(kids) - 1)

        for root in roots:
            dfs(root)

        return "\n".join(lines)

    def get_stats(self) -> dict:
        return {
            "total_layouts": len(self._layouts),
            "total_nodes": sum(len(l.nodes) for l in self._layouts.values()),
            "total_edges": sum(len(l.edges) for l in self._layouts.values()),
        }
