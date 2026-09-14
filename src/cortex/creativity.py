"""Creativity Engine — 创造力引擎。

Agent能：
- 生成多个设计方案供选择
- 跨领域联想（从不同领域借鉴方案）
- 约束突破（在限制条件下找到创新解法）
- 组合创新（把已有方案重新组合）
"""

import json
import logging
import time
from typing import Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


class CreativityEngine:
    """创造力引擎：生成创新方案"""

    def __init__(self, base_url: str = "", api_key: str = "", model: str = ""):
        self.base_url = base_url or settings.llm_base_url
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model

    async def generate_alternatives(self, problem: str, constraints: list[str] = None, count: int = 3) -> list[dict]:
        """生成多个替代方案"""
        if not self.api_key:
            return [{"approach": "无LLM配置", "description": "需要配置LLM API"}]

        constraint_text = ""
        if constraints:
            constraint_text = f"\n约束条件：\n" + "\n".join(f"- {c}" for c in constraints)

        prompt = f"""你是一个创新方案生成器。针对以下问题，生成{count}个不同的解决方案。

问题：{problem}{constraint_text}

返回JSON数组，每个元素：
- approach: 方案名称（简短）
- description: 方案描述（50-100字）
- pros: 优点列表
- cons: 缺点列表
- novelty: 新颖度（1-5，5最创新）
- feasibility: 可行性（1-5，5最可行）

只返回JSON数组，不要其他文字。"""

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": "你是创新方案生成器，擅长跨领域思考。"},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.8,  # 高温度鼓励创新
                        "max_tokens": 2048,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = content.strip()
            if content.startswith("```"):
                content = content[content.index("\n")+1:]
            if content.endswith("```"):
                content = content[:-3]
            return json.loads(content.strip())
        except Exception as e:
            logger.error("创造力引擎调用失败: %s", e)
            return [{"approach": "错误", "description": str(e)}]

    async def cross_domain_idea(self, domain: str, problem: str) -> dict:
        """跨领域联想：从其他领域借鉴方案"""
        prompt = f"""从"{domain}"领域之外的3个不同领域，借鉴解决类似问题的方案。

问题：{problem}

返回JSON：
- ideas: 数组，每个包含 domain(借鉴领域), approach(方案), analogy(类比说明)
- best_idea: 最有潜力的方案名称

只返回JSON。"""

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": "你是跨领域创新顾问，擅长从不同领域借鉴灵感。"},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.9,
                        "max_tokens": 1024,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = content.strip()
            if content.startswith("```"):
                content = content[content.index("\n")+1:]
            if content.endswith("```"):
                content = content[:-3]
            return json.loads(content.strip())
        except Exception as e:
            logger.error("跨领域联想失败: %s", e)
            return {"ideas": [], "error": str(e)}
