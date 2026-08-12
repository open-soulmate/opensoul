"""A2A Task Manager - manages task lifecycle and agent execution."""

import asyncio
import logging
from uuid import uuid4
from datetime import datetime

from src.a2a.models import (
    Task, TaskStatus, Message, Artifact,
    AgentCard, DEFAULT_AGENT_CARD,
    ErrorCode, JSONRPCError,
)

logger = logging.getLogger(__name__)


class TaskManager:
    """In-memory task manager for A2A protocol."""

    def __init__(self):
        self.tasks: dict[str, Task] = {}
        self.agent_card: AgentCard = DEFAULT_AGENT_CARD

    def get_agent_card(self) -> AgentCard:
        return self.agent_card

    def set_agent_card(self, card: AgentCard):
        self.agent_card = card

    async def create_task(self, message: Message) -> Task:
        """Create a new task from a user message."""
        task = Task(
            status=TaskStatus(state="submitted"),
            history=[message],
        )
        self.tasks[task.id] = task
        logger.info(f"Task created: {task.id}")
        return task

    async def process_task(self, task_id: str, message: Message) -> Task:
        """Process a task - send message and get response."""
        task = self.tasks.get(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        # Add user message to history
        task.history.append(message)
        task.status = TaskStatus(state="working")

        try:
            # Extract text from message parts
            user_text = self._extract_text(message)

            # Process based on skills
            response_text = await self._process_message(user_text, task)

            # Create agent response
            agent_msg = Message(
                role="agent",
                parts=[{"type": "text", "text": response_text}],
                taskId=task.id,
                contextId=task.contextId,
            )
            task.history.append(agent_msg)
            task.status = TaskStatus(state="completed", message=agent_msg)

        except Exception as e:
            logger.error(f"Task processing failed: {e}")
            task.status = TaskStatus(
                state="failed",
                message=Message(
                    role="agent",
                    parts=[{"type": "text", "text": f"Error: {str(e)}"}],
                    taskId=task.id,
                ),
            )

        return task

    async def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    async def cancel_task(self, task_id: str) -> Task | None:
        task = self.tasks.get(task_id)
        if not task:
            return None
        if task.status.state in ("completed", "failed", "canceled"):
            return None
        task.status = TaskStatus(state="canceled")
        return task

    def _extract_text(self, message: Message) -> str:
        """Extract text from message parts."""
        texts = []
        for part in message.parts:
            if part.get("type") == "text":
                texts.append(part.get("text", ""))
        return "\n".join(texts)

    async def _process_message(self, text: str, task: Task) -> str:
        """Process a message and return response. Override for custom logic."""
        # Default: echo back with skill routing
        for skill in self.agent_card.skills:
            for tag in skill.tags:
                if tag.lower() in text.lower():
                    return await self._handle_skill(skill.id, text, task)

        return f"收到您的消息：{text}\n\n可用技能：{', '.join(s.name for s in self.agent_card.skills)}"

    async def _handle_skill(self, skill_id: str, text: str, task: Task) -> str:
        """Handle a specific skill. Override for custom logic."""
        handlers = {
            "knowledge": self._handle_knowledge,
            "chat": self._handle_chat,
            "search": self._handle_search,
            "graph": self._handle_graph,
        }
        handler = handlers.get(skill_id, self._handle_default)
        return await handler(text, task)

    async def _handle_knowledge(self, text: str, task: Task) -> str:
        return f"[知识库] 处理请求：{text}\n\n知识库功能正在开发中。"

    async def _handle_chat(self, text: str, task: Task) -> str:
        return f"[对话] {text}\n\nAI对话功能正在开发中。"

    async def _handle_search(self, text: str, task: Task) -> str:
        return f"[搜索] 搜索请求：{text}\n\n搜索功能正在开发中。"

    async def _handle_graph(self, text: str, task: Task) -> str:
        return f"[图谱] 图谱请求：{text}\n\n图谱功能正在开发中。"

    async def _handle_default(self, text: str, task: Task) -> str:
        return f"收到：{text}"


# Global task manager instance
task_manager = TaskManager()
