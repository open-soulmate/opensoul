"""OpenWill workflow execution engine."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .models import (
    ExecutionStatus,
    NodeType,
    StepExecution,
    TriggerType,
    Workflow,
    WorkflowEdge,
    WorkflowExecution,
    WorkflowNode,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)


class WorkflowEngine:
    """In-memory workflow execution engine with DAG traversal."""

    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}
        self._executions: dict[str, WorkflowExecution] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._max_execution_history = 1000

    # ── Workflow CRUD ───────────────────────────────────────────

    def create_workflow(
        self,
        name: str,
        description: str = "",
        trigger: TriggerType = TriggerType.MANUAL,
        trigger_config: dict | None = None,
        variables: dict | None = None,
    ) -> Workflow:
        wf_id = f"wf_{uuid4().hex[:12]}"
        wf = Workflow(
            id=wf_id,
            name=name,
            description=description,
            trigger=trigger,
            trigger_config=trigger_config or {},
            variables=variables or {},
        )
        self._workflows[wf_id] = wf
        return wf

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        return self._workflows.get(workflow_id)

    def list_workflows(self, status: WorkflowStatus | None = None) -> list[Workflow]:
        wfs = list(self._workflows.values())
        if status:
            wfs = [w for w in wfs if w.status == status]
        return sorted(wfs, key=lambda w: w.updated_at, reverse=True)

    def update_workflow(self, workflow_id: str, **kwargs: Any) -> Workflow | None:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None
        for key, value in kwargs.items():
            if hasattr(wf, key) and key not in ("id", "created_at"):
                setattr(wf, key, value)
        wf.updated_at = datetime.now(timezone.utc).isoformat()
        return wf

    def delete_workflow(self, workflow_id: str) -> bool:
        return self._workflows.pop(workflow_id, None) is not None

    # ── Node/Edge Management ────────────────────────────────────

    def add_node(
        self,
        workflow_id: str,
        node_type: NodeType,
        label: str = "",
        config: dict | None = None,
        position: dict | None = None,
    ) -> WorkflowNode | None:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None
        node = WorkflowNode(
            id=f"node_{uuid4().hex[:8]}",
            node_type=node_type,
            label=label,
            config=config or {},
            position=position or {"x": 0, "y": 0},
        )
        wf.nodes.append(node)
        wf.updated_at = datetime.now(timezone.utc).isoformat()
        return node

    def remove_node(self, workflow_id: str, node_id: str) -> bool:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return False
        before = len(wf.nodes)
        wf.nodes = [n for n in wf.nodes if n.id != node_id]
        wf.edges = [e for e in wf.edges if e.source_node_id != node_id and e.target_node_id != node_id]
        wf.updated_at = datetime.now(timezone.utc).isoformat()
        return len(wf.nodes) < before

    def add_edge(
        self,
        workflow_id: str,
        source_node_id: str,
        target_node_id: str,
        condition: str | None = None,
        label: str = "",
    ) -> WorkflowEdge | None:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None
        # Validate nodes exist
        if not wf.get_node(source_node_id) or not wf.get_node(target_node_id):
            return None
        edge = WorkflowEdge(
            id=f"edge_{uuid4().hex[:8]}",
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            condition=condition,
            label=label,
        )
        wf.edges.append(edge)
        wf.updated_at = datetime.now(timezone.utc).isoformat()
        return edge

    def remove_edge(self, workflow_id: str, edge_id: str) -> bool:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return False
        before = len(wf.edges)
        wf.edges = [e for e in wf.edges if e.id != edge_id]
        wf.updated_at = datetime.now(timezone.utc).isoformat()
        return len(wf.edges) < before

    # ── Execution ───────────────────────────────────────────────

    def execute(self, workflow_id: str, input_vars: dict | None = None) -> WorkflowExecution | None:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None

        # Validate
        errors = wf.validate_dag()
        if errors:
            exec_id = f"exec_{uuid4().hex[:12]}"
            return WorkflowExecution(
                id=exec_id,
                workflow_id=workflow_id,
                workflow_name=wf.name,
                status=ExecutionStatus.FAILED,
                error=f"Validation failed: {'; '.join(errors)}",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        exec_id = f"exec_{uuid4().hex[:12]}"
        variables = {**wf.variables, **(input_vars or {})}
        execution = WorkflowExecution(
            id=exec_id,
            workflow_id=workflow_id,
            workflow_name=wf.name,
            variables=variables,
            trigger_type="manual",
        )

        self._executions[exec_id] = execution
        wf.run_count += 1
        wf.last_run_at = datetime.now(timezone.utc).isoformat()

        # Run synchronously (for now — async execution is via API)
        self._run_execution(wf, execution)
        return execution

    def _run_execution(self, wf: Workflow, execution: WorkflowExecution) -> None:
        """Execute the workflow DAG from trigger nodes."""
        execution.status = ExecutionStatus.RUNNING
        triggers = wf.get_trigger_nodes()
        if not triggers:
            execution.status = ExecutionStatus.FAILED
            execution.error = "No trigger node found"
            execution.completed_at = datetime.now(timezone.utc).isoformat()
            return

        # BFS traversal
        queue: list[str] = [t.id for t in triggers]
        visited: set[str] = set()
        max_steps = 200  # Safety limit
        step_count = 0

        while queue and step_count < max_steps:
            node_id = queue.pop(0)
            if node_id in visited:
                continue
            visited.add(node_id)
            step_count += 1

            node = wf.get_node(node_id)
            if not node:
                continue

            # Create step execution
            step = StepExecution(
                node_id=node_id,
                node_label=node.display_label,
                status=ExecutionStatus.RUNNING,
                started_at=datetime.now(timezone.utc).isoformat(),
                input_data=dict(execution.variables),
            )
            execution.steps.append(step)

            # Execute node based on type
            try:
                result = self._execute_node(node, execution.variables)
                step.output_data = result
                step.status = ExecutionStatus.SUCCESS
                execution.variables.update(result)
            except Exception as e:
                step.status = ExecutionStatus.FAILED
                step.error = str(e)
                execution.status = ExecutionStatus.FAILED
                execution.error = f"Node '{node.display_label}' failed: {e}"
                execution.completed_at = datetime.now(timezone.utc).isoformat()
                return

            step.completed_at = datetime.now(timezone.utc).isoformat()
            if step.started_at and step.completed_at:
                step.duration_ms = self._calc_duration_ms(step.started_at, step.completed_at)

            # Find next nodes
            outgoing = wf.get_outgoing_edges(node_id)
            for edge in outgoing:
                if edge.condition:
                    # Evaluate condition
                    try:
                        if self._eval_condition(edge.condition, execution.variables):
                            queue.append(edge.target_node_id)
                    except Exception:
                        pass  # Condition eval failure = skip edge
                else:
                    queue.append(edge.target_node_id)

        if execution.status != ExecutionStatus.FAILED:
            execution.status = ExecutionStatus.SUCCESS
        execution.completed_at = datetime.now(timezone.utc).isoformat()

        # Trim history
        self._trim_history()

    def _execute_node(self, node: WorkflowNode, variables: dict[str, Any]) -> dict[str, Any]:
        """Execute a single node and return output data."""
        if node.node_type == NodeType.TRIGGER:
            return {"triggered": True}
        elif node.node_type == NodeType.DELAY:
            seconds = node.config.get("seconds", 0)
            return {"delayed_seconds": seconds}
        elif node.node_type == NodeType.CONDITION:
            expr = node.config.get("expression", "True")
            result = self._eval_condition(expr, variables)
            return {"condition_result": result}
        elif node.node_type == NodeType.ACTION:
            action_type = node.config.get("type", "script")
            if action_type == "http":
                return {"action": "http", "url": node.config.get("url", ""), "status": "simulated"}
            elif action_type == "llm":
                prompt = self._interpolate(node.config.get("prompt", ""), variables)
                return {"action": "llm", "prompt": prompt, "response": "[simulated LLM response]"}
            elif action_type == "script":
                return {"action": "script", "executed": True}
            elif action_type == "notify":
                return {"action": "notify", "sent": True}
            else:
                return {"action": action_type, "executed": True}
        elif node.node_type in (NodeType.PARALLEL, NodeType.MERGE):
            return {"parallel": True}
        elif node.node_type == NodeType.END:
            return {"ended": True}
        return {}

    @staticmethod
    def _eval_condition(expr: str, variables: dict[str, Any]) -> bool:
        """Safely evaluate a condition expression."""
        safe_globals = {"__builtins__": {}}
        try:
            return bool(eval(expr, safe_globals, variables))
        except Exception:
            return False

    @staticmethod
    def _interpolate(template: str, variables: dict[str, Any]) -> str:
        """Interpolate ${var} placeholders in a string."""
        for key, value in variables.items():
            template = template.replace(f"${{{key}}}", str(value))
        return template

    @staticmethod
    def _calc_duration_ms(start: str, end: str) -> float:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        return (e - s).total_seconds() * 1000

    def _trim_history(self) -> None:
        if len(self._executions) > self._max_execution_history:
            sorted_execs = sorted(
                self._executions.items(),
                key=lambda x: x[1].started_at,
                reverse=True,
            )
            self._executions = dict(sorted_execs[: self._max_execution_history])

    # ── Execution History ───────────────────────────────────────

    def get_execution(self, execution_id: str) -> WorkflowExecution | None:
        return self._executions.get(execution_id)

    def list_executions(
        self, workflow_id: str | None = None, limit: int = 50
    ) -> list[WorkflowExecution]:
        execs = list(self._executions.values())
        if workflow_id:
            execs = [e for e in execs if e.workflow_id == workflow_id]
        return sorted(execs, key=lambda e: e.started_at, reverse=True)[:limit]

    def cancel_execution(self, execution_id: str) -> bool:
        exec = self._executions.get(execution_id)
        if not exec or exec.status not in (ExecutionStatus.PENDING, ExecutionStatus.RUNNING):
            return False
        exec.status = ExecutionStatus.CANCELLED
        exec.completed_at = datetime.now(timezone.utc).isoformat()
        return True

    # ── Stats ───────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        total_execs = len(self._executions)
        success = sum(1 for e in self._executions.values() if e.status == ExecutionStatus.SUCCESS)
        failed = sum(1 for e in self._executions.values() if e.status == ExecutionStatus.FAILED)
        running = sum(1 for e in self._executions.values() if e.status == ExecutionStatus.RUNNING)
        return {
            "total_workflows": len(self._workflows),
            "active_workflows": sum(1 for w in self._workflows.values() if w.status == WorkflowStatus.ACTIVE),
            "total_executions": total_execs,
            "successful": success,
            "failed": failed,
            "running": running,
            "success_rate": round(success / total_execs * 100, 1) if total_execs else 0,
        }
