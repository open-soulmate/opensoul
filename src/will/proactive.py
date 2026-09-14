"""Proactive Engine — 主动行为引擎。

Agent不等指令，主动观察、判断、行动：
- 监听文件变化 → 自动备份/检查
- 监听错误日志 → 自动诊断
- 定期学习 → 提取模式
- 发现问题 → 主动报告
"""

import asyncio
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ProactiveEngine:
    """主动行为引擎 — 观察→判断→行动循环"""

    def __init__(self, db_pool=None, tenant_id: str = "default", agent_id: str = "default"):
        self.db = db_pool
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self._watchers: dict[str, Callable] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def register_watcher(self, name: str, callback: Callable):
        """注册一个观察者"""
        self._watchers[name] = callback

    async def start(self, interval_seconds: int = 60):
        """启动主动观察循环"""
        self._running = True
        self._task = asyncio.create_task(self._observe_loop(interval_seconds))
        logger.info("主动行为引擎启动, interval=%ds, watchers=%d", interval_seconds, len(self._watchers))

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _observe_loop(self, interval: int):
        while self._running:
            try:
                for name, callback in self._watchers.items():
                    try:
                        await callback()
                    except Exception as e:
                        logger.debug("观察者 %s 异常: %s", name, e)
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("观察循环异常: %s", e)
                await asyncio.sleep(interval)

    async def get_status(self) -> dict:
        return {
            "running": self._running,
            "watchers": list(self._watchers.keys()),
        }
