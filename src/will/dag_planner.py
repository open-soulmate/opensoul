"""DAG task planner for OpenWill.

Decomposes complex tasks into directed acyclic graphs (DAGs) for
parallel execution with dependency management.

Borrowed from: LLM Compiler, Plan-and-Execute, Tree of Thoughts
"""

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional

logger = logging.getLogger("opensoul.will.planner")


class StepStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    step_id: str
    description: str
    tool_name: str = ""
    arguments: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    error: str = ""
    estimated_duration: float = 0.0
    actual_duration: float = 0.0
    retry_count: int = 0


@dataclass
class ExecutionPlan:
    plan_id: str
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0
    status: str = "pending"
    metadata: dict = field(default_factory=dict)

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0.0
        completed = sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)
        return completed / len(self.steps)

    @property
    def is_complete(self) -> bool:
        return all(
            s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED, StepStatus.FAILED)
            for s in self.steps
        )

    def get_ready_steps(self) -> list[PlanStep]:
        """Get steps whose dependencies are all satisfied."""
        completed_ids = {s.step_id for s in self.steps if s.status == StepStatus.COMPLETED}
        return [
            s
            for s in self.steps
            if s.status == StepStatus.PENDING and all(dep in completed_ids for dep in s.depends_on)
        ]


class DAGPlanner:
    """Creates and manages DAG-based execution plans."""

    def __init__(self):
        self._plans: dict[str, ExecutionPlan] = {}
        self._stats = {
            "total_plans": 0,
            "completed_plans": 0,
            "failed_plans": 0,
            "total_steps": 0,
        }

    def create_plan(self, goal: str, steps_spec: list[dict]) -> ExecutionPlan:
        """Create an execution plan from step specifications."""
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"

        steps = []
        for i, spec in enumerate(steps_spec):
            step = PlanStep(
                step_id=spec.get("id", f"step_{i}"),
                description=spec.get("description", ""),
                tool_name=spec.get("tool", ""),
                arguments=spec.get("arguments", {}),
                depends_on=spec.get("depends_on", []),
                estimated_duration=spec.get("estimated_seconds", 0),
            )
            steps.append(step)

        plan = ExecutionPlan(plan_id=plan_id, goal=goal, steps=steps)
        self._plans[plan_id] = plan
        self._stats["total_plans"] += 1
        self._stats["total_steps"] += len(steps)

        logger.info(f"Created plan {plan_id}: {len(steps)} steps for: {goal[:50]}")
        return plan

    def create_from_llm_response(self, goal: str, llm_response: str) -> ExecutionPlan | None:
        """Parse LLM response into an execution plan."""
        # Try JSON format
        try:
            data = json.loads(llm_response)
            if isinstance(data, dict) and "steps" in data:
                return self.create_plan(goal, data["steps"])
            elif isinstance(data, list):
                return self.create_plan(goal, data)
        except json.JSONDecodeError:
            pass

        # Parse text format
        steps_spec = []
        current: dict = {}

        for line in llm_response.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith(("Step ", "步骤 ", "- Step", "1.", "2.", "3.", "4.", "5.")):
                if current:
                    steps_spec.append(current)
                current = {"id": f"step_{len(steps_spec)}", "description": line}
            elif current and ("depends" in line.lower() or "依赖" in line):
                import re

                deps = re.findall(r"step[_\s]*(\d+)", line, re.IGNORECASE)
                current["depends_on"] = [f"step_{d}" for d in deps]
            elif current:
                current["description"] += f" {line}"

        if current:
            steps_spec.append(current)

        if steps_spec:
            return self.create_plan(goal, steps_spec)

        logger.warning(f"Could not parse LLM response as plan: {llm_response[:200]}")
        return None

    def get_plan(self, plan_id: str) -> ExecutionPlan | None:
        return self._plans.get(plan_id)

    def update_step(
        self,
        plan_id: str,
        step_id: str,
        status: StepStatus,
        result: Any = None,
        error: str = "",
    ):
        """Update a step's status and check plan completion."""
        plan = self._plans.get(plan_id)
        if not plan:
            return

        for step in plan.steps:
            if step.step_id == step_id:
                step.status = status
                step.result = result
                step.error = error
                break

        if plan.is_complete:
            plan.completed_at = time.time()
            failed = sum(1 for s in plan.steps if s.status == StepStatus.FAILED)
            if failed == 0:
                plan.status = "completed"
                self._stats["completed_plans"] += 1
            else:
                plan.status = "failed"
                self._stats["failed_plans"] += 1

    def visualize_ascii(self, plan_id: str) -> str:
        """Render plan as ASCII DAG tree."""
        plan = self._plans.get(plan_id)
        if not plan:
            return "Plan not found"

        lines = [f"📋 Plan: {plan.goal}"]
        lines.append(f"   Status: {plan.status} | Progress: {plan.progress:.0%}")
        lines.append("")

        for step in plan.steps:
            icon = {
                StepStatus.PENDING: "⏳",
                StepStatus.READY: "🔵",
                StepStatus.RUNNING: "🔄",
                StepStatus.COMPLETED: "✅",
                StepStatus.FAILED: "❌",
                StepStatus.SKIPPED: "⏭️",
            }.get(step.status, "❓")
            deps = f" (deps: {', '.join(step.depends_on)})" if step.depends_on else ""
            lines.append(f"  {icon} {step.step_id}: {step.description[:60]}{deps}")
            if step.error:
                lines.append(f"     ⚠️ {step.error[:80]}")

        return "\n".join(lines)

    def to_mermaid(self, plan_id: str) -> str:
        """Export plan as Mermaid diagram."""
        plan = self._plans.get(plan_id)
        if not plan:
            return "graph TD\n    empty[No data]"

        lines = ["graph TD"]
        for step in plan.steps:
            icon = {
                StepStatus.PENDING: "⏳",
                StepStatus.RUNNING: "🔄",
                StepStatus.COMPLETED: "✅",
                StepStatus.FAILED: "❌",
            }.get(step.status, "")
            safe = step.description.replace('"', "'")[:40]
            lines.append(f'    {step.step_id}["{icon} {safe}"]')

        for step in plan.steps:
            for dep in step.depends_on:
                lines.append(f"    {dep} --> {step.step_id}")

        return "\n".join(lines)

    def get_stats(self) -> dict:
        return {
            **self._stats,
            "active_plans": sum(
                1 for p in self._plans.values() if p.status in ("pending", "running")
            ),
        }
