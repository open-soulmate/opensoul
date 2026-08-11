import json
from dataclasses import dataclass, field

import httpx

from src.config import settings


@dataclass
class Task:
    description: str
    dependencies: list[int] = field(default_factory=list)
    priority: int = 1  # 1=high, 2=medium, 3=low


class TaskPlanner:
    """Decomposes complex goals into ordered sub-tasks via LLM."""

    SYSTEM_PROMPT = """You are a task planning engine. Given a complex goal, decompose it into concrete, actionable sub-tasks.

Return a JSON array where each element has:
- "description": a clear, concise task description
- "dependencies": array of 0-based indices of tasks that must complete before this one
- "priority": 1 (high), 2 (medium), or 3 (low)

Rules:
- Tasks should be atomic and independently executable
- Respect logical ordering (dependencies flow forward)
- Aim for 3-8 tasks for a typical goal
- Return ONLY the JSON array, no other text"""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        self.base_url = base_url or settings.llm_base_url
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model

    async def plan(self, goal: str) -> list[Task]:
        if not self.api_key:
            raise ValueError("LLM API key not configured")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": f"Goal: {goal}"},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2048,
                },
                timeout=120,
            )
            resp.raise_for_status()

        content = resp.json()["choices"][0]["message"]["content"]
        raw_tasks = json.loads(self._strip_code_fences(content))

        tasks = []
        for t in raw_tasks:
            tasks.append(Task(
                description=t["description"],
                dependencies=t.get("dependencies", []),
                priority=t.get("priority", 2),
            ))
        return tasks

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            first_newline = text.index("\n")
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()
