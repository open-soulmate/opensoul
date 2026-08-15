"""OpenWill workflow execution engine — real cross-component execution.

Actions now make real calls:
- http: actual HTTP requests via httpx
- llm: calls OpenGland /api/gland/chat for real LLM responses
- notify: calls OpenEcho /api/echo/send for real notifications
- knowledge_search: calls OpenSoul /api/search
- script: executes shell commands with safety constraints
- organ: calls any organ's API endpoint
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

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

_BASE = "http://127.0.0.1:8090"

# Safety: blocked shell commands
_BLOCKED_COMMANDS = frozenset({
    "rm -rf /", "mkfs", "dd if=", ":(){ :|:& };:",
    "shutdown", "reboot", "halt", "poweroff",
    "chmod 777 /", "chown root",
})


class WorkflowEngine:
    """In-memory workflow execution engine with DAG traversal and real action execution."""

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

    async def execute_async(self, workflow_id: str, input_vars: dict | None = None) -> WorkflowExecution | None:
        """Execute workflow asynchronously with real action execution."""
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None

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

        # Run asynchronously with real I/O
        await self._run_execution_async(wf, execution)
        return execution

    def execute(self, workflow_id: str, input_vars: dict | None = None) -> WorkflowExecution | None:
        """Synchronous wrapper — creates an event loop task."""
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None

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

        # Try to run in existing loop, fallback to sync
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._run_execution_async(wf, execution))
        except RuntimeError:
            asyncio.run(self._run_execution_async(wf, execution))
        return execution

    async def _run_execution_async(self, wf: Workflow, execution: WorkflowExecution) -> None:
        """Execute the workflow DAG from trigger nodes with real async I/O."""
        execution.status = ExecutionStatus.RUNNING
        triggers = wf.get_trigger_nodes()
        if not triggers:
            execution.status = ExecutionStatus.FAILED
            execution.error = "No trigger node found"
            execution.completed_at = datetime.now(timezone.utc).isoformat()
            return

        async with httpx.AsyncClient(timeout=60.0) as client:
            queue: list[str] = [t.id for t in triggers]
            visited: set[str] = set()
            max_steps = 200
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

                step = StepExecution(
                    node_id=node_id,
                    node_label=node.display_label,
                    status=ExecutionStatus.RUNNING,
                    started_at=datetime.now(timezone.utc).isoformat(),
                    input_data=dict(execution.variables),
                )
                execution.steps.append(step)

                try:
                    result = await self._execute_node_async(node, execution.variables, client)
                    step.output_data = result
                    step.status = ExecutionStatus.SUCCESS
                    execution.variables.update(result)
                except Exception as e:
                    step.status = ExecutionStatus.FAILED
                    step.error = str(e)
                    execution.status = ExecutionStatus.FAILED
                    execution.error = f"Node '{node.display_label}' failed: {e}"
                    execution.completed_at = datetime.now(timezone.utc).isoformat()
                    logger.error("Workflow node '%s' failed: %s", node.display_label, e)
                    return

                step.completed_at = datetime.now(timezone.utc).isoformat()
                if step.started_at and step.completed_at:
                    step.duration_ms = self._calc_duration_ms(step.started_at, step.completed_at)

                # Find next nodes
                outgoing = wf.get_outgoing_edges(node_id)
                for edge in outgoing:
                    if edge.condition:
                        try:
                            if self._eval_condition(edge.condition, execution.variables):
                                queue.append(edge.target_node_id)
                        except Exception:
                            pass
                    else:
                        queue.append(edge.target_node_id)

        if execution.status != ExecutionStatus.FAILED:
            execution.status = ExecutionStatus.SUCCESS
        execution.completed_at = datetime.now(timezone.utc).isoformat()
        self._trim_history()

    async def _execute_node_async(
        self, node: WorkflowNode, variables: dict[str, Any], client: httpx.AsyncClient
    ) -> dict[str, Any]:
        """Execute a single node with real cross-component calls."""
        if node.node_type == NodeType.TRIGGER:
            return {"triggered": True}

        elif node.node_type == NodeType.DELAY:
            seconds = node.config.get("seconds", 0)
            if seconds > 0:
                await asyncio.sleep(min(seconds, 300))  # Cap at 5 min
            return {"delayed_seconds": seconds}

        elif node.node_type == NodeType.CONDITION:
            expr = node.config.get("expression", "True")
            result = self._eval_condition(expr, variables)
            return {"condition_result": result}

        elif node.node_type == NodeType.ACTION:
            return await self._execute_action(node, variables, client)

        elif node.node_type in (NodeType.PARALLEL, NodeType.MERGE):
            return {"parallel": True}

        elif node.node_type == NodeType.END:
            return {"ended": True}

        return {}

    async def _execute_action(
        self, node: WorkflowNode, variables: dict[str, Any], client: httpx.AsyncClient
    ) -> dict[str, Any]:
        """Execute an action node — real cross-component calls."""
        action_type = node.config.get("type", "script")
        timeout = node.config.get("timeout", 30)

        if action_type == "http":
            return await self._action_http(node, variables, client, timeout)
        elif action_type == "llm":
            return await self._action_llm(node, variables, client, timeout)
        elif action_type == "notify":
            return await self._action_notify(node, variables, client)
        elif action_type == "knowledge_search":
            return await self._action_knowledge_search(node, variables, client)
        elif action_type == "script":
            return await self._action_script(node, variables, timeout)
        elif action_type == "organ":
            return await self._action_organ(node, variables, client, timeout)
        else:
            return {"action": action_type, "executed": True, "warning": f"Unknown action type: {action_type}"}

    # ── Real Action Implementations ─────────────────────────────

    async def _action_http(
        self, node: WorkflowNode, variables: dict[str, Any], client: httpx.AsyncClient, timeout: int
    ) -> dict[str, Any]:
        """Make a real HTTP request."""
        url = self._interpolate(node.config.get("url", ""), variables)
        method = node.config.get("method", "GET").upper()
        headers = node.config.get("headers", {})
        body = node.config.get("body")

        if body and isinstance(body, str):
            body = self._interpolate(body, variables)

        if not url:
            raise ValueError("HTTP action requires 'url' in config")

        try:
            resp = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=body if body else None,
                timeout=timeout,
            )
            # Try to parse JSON response
            try:
                resp_data = resp.json()
            except Exception:
                resp_data = resp.text[:2000]

            return {
                "action": "http",
                "url": url,
                "method": method,
                "status_code": resp.status_code,
                "response": resp_data,
                "success": 200 <= resp.status_code < 400,
            }
        except httpx.TimeoutException:
            raise ValueError(f"HTTP request to {url} timed out after {timeout}s")
        except Exception as e:
            raise ValueError(f"HTTP request failed: {e}")

    async def _action_llm(
        self, node: WorkflowNode, variables: dict[str, Any], client: httpx.AsyncClient, timeout: int
    ) -> dict[str, Any]:
        """Call OpenGland for a real LLM response."""
        prompt = self._interpolate(node.config.get("prompt", ""), variables)
        model = node.config.get("model")
        system = self._interpolate(node.config.get("system", ""), variables)
        temperature = node.config.get("temperature", 0.7)
        max_tokens = node.config.get("max_tokens", 2048)

        if not prompt:
            raise ValueError("LLM action requires 'prompt' in config")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if model:
            payload["model"] = model

        try:
            resp = await client.post(
                f"{_BASE}/api/gland/chat",
                json=payload,
                timeout=timeout,
            )
            if resp.status_code != 200:
                raise ValueError(f"Gland returned {resp.status_code}: {resp.text[:500]}")

            data = resp.json()
            # Extract the response content
            content = ""
            if isinstance(data, dict):
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                elif "content" in data:
                    content = data["content"]
                elif "response" in data:
                    content = data["response"]

            return {
                "action": "llm",
                "prompt": prompt[:200],
                "response": content[:5000],
                "model": data.get("model", model or "unknown"),
                "usage": data.get("usage", {}),
            }
        except httpx.TimeoutException:
            raise ValueError(f"LLM call timed out after {timeout}s")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"LLM call failed: {e}")

    async def _action_notify(
        self, node: WorkflowNode, variables: dict[str, Any], client: httpx.AsyncClient
    ) -> dict[str, Any]:
        """Send a real notification via OpenEcho."""
        title = self._interpolate(node.config.get("title", "Workflow Notification"), variables)
        content = self._interpolate(node.config.get("content", ""), variables)
        channel = node.config.get("channel", "webhook")
        target = node.config.get("target", "")
        priority = node.config.get("priority", "normal")

        if not content:
            raise ValueError("Notify action requires 'content' in config")

        try:
            resp = await client.post(
                f"{_BASE}/api/echo/send",
                json={
                    "title": title,
                    "content": content,
                    "channel": channel,
                    "target": target,
                    "priority": priority,
                },
                timeout=10.0,
            )
            data = resp.json() if resp.status_code == 200 else {"error": resp.text[:500]}
            return {
                "action": "notify",
                "title": title,
                "channel": channel,
                "success": data.get("success", False),
                "msg_id": data.get("msg_id"),
                "error": data.get("error"),
            }
        except Exception as e:
            raise ValueError(f"Notification failed: {e}")

    async def _action_knowledge_search(
        self, node: WorkflowNode, variables: dict[str, Any], client: httpx.AsyncClient
    ) -> dict[str, Any]:
        """Search the knowledge base via OpenSoul."""
        query = self._interpolate(node.config.get("query", ""), variables)
        limit = node.config.get("limit", 5)

        if not query:
            raise ValueError("Knowledge search requires 'query' in config")

        try:
            resp = await client.get(
                f"{_BASE}/api/search",
                params={"q": query, "limit": limit},
                timeout=15.0,
            )
            data = resp.json() if resp.status_code == 200 else {"results": []}
            results = data.get("results", data.get("hits", []))
            return {
                "action": "knowledge_search",
                "query": query,
                "results": results[:limit],
                "count": len(results),
            }
        except Exception as e:
            raise ValueError(f"Knowledge search failed: {e}")

    async def _action_script(
        self, node: WorkflowNode, variables: dict[str, Any], timeout: int
    ) -> dict[str, Any]:
        """Execute a shell command with safety constraints."""
        cmd = self._interpolate(node.config.get("command", ""), variables)
        cwd = node.config.get("cwd")

        if not cmd:
            raise ValueError("Script action requires 'command' in config")

        # Safety check
        for blocked in _BLOCKED_COMMANDS:
            if blocked in cmd:
                raise ValueError(f"Blocked dangerous command: {blocked}")

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode(errors="replace") if stdout else ""

            return {
                "action": "script",
                "command": cmd[:200],
                "exit_code": proc.returncode,
                "output": output[:5000],
                "success": proc.returncode == 0,
            }
        except asyncio.TimeoutError:
            raise ValueError(f"Script timed out after {timeout}s")
        except Exception as e:
            raise ValueError(f"Script execution failed: {e}")

    async def _action_organ(
        self, node: WorkflowNode, variables: dict[str, Any], client: httpx.AsyncClient, timeout: int
    ) -> dict[str, Any]:
        """Call any organ's API endpoint directly."""
        endpoint = self._interpolate(node.config.get("endpoint", ""), variables)
        method = node.config.get("method", "GET").upper()
        body = node.config.get("body")

        if not endpoint:
            raise ValueError("Organ action requires 'endpoint' in config (e.g. '/api/vein/stats')")

        url = f"{_BASE}{endpoint}"
        if body and isinstance(body, dict):
            # Interpolate variables in body
            body = {k: self._interpolate(str(v), variables) for k, v in body.items()}

        try:
            resp = await client.request(
                method=method,
                url=url,
                json=body if body and method in ("POST", "PUT", "PATCH") else None,
                params=body if body and method == "GET" else None,
                timeout=timeout,
            )
            try:
                data = resp.json()
            except Exception:
                data = resp.text[:2000]

            return {
                "action": "organ",
                "endpoint": endpoint,
                "method": method,
                "status_code": resp.status_code,
                "response": data,
                "success": resp.status_code == 200,
            }
        except httpx.TimeoutException:
            raise ValueError(f"Organ call to {endpoint} timed out after {timeout}s")
        except Exception as e:
            raise ValueError(f"Organ call failed: {e}")

    # ── Helpers ─────────────────────────────────────────────────

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
        if not template:
            return template
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
