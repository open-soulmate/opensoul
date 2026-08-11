import httpx

from src.config import settings


class BaseAgent:
    name: str = "base"
    system_prompt: str = ""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    async def run(self, user_input: str) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": user_input},
                    ],
                    "temperature": 0.4,
                    "max_tokens": 2048,
                },
                timeout=120,
            )
            resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


class Researcher(BaseAgent):
    name = "researcher"
    system_prompt = (
        "You are a Researcher agent. Your job is to gather relevant information, "
        "facts, and context about the given topic. Provide comprehensive, accurate "
        "findings organized with clear bullet points. Focus on breadth of coverage."
    )


class Analyzer(BaseAgent):
    name = "analyzer"
    system_prompt = (
        "You are an Analyzer agent. Given research findings, your job is to identify "
        "patterns, extract key insights, evaluate significance, and draw analytical "
        "conclusions. Be critical and structured in your analysis."
    )


class Writer(BaseAgent):
    name = "writer"
    system_prompt = (
        "You are a Writer agent. Given research findings and analysis, produce a "
        "clear, well-structured final output. Write in a professional yet accessible "
        "tone. Use headings, bullet points, or numbered lists as appropriate."
    )


class MultiAgent:
    """Orchestrates a Researcher → Analyzer → Writer pipeline."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        url = base_url or settings.llm_base_url
        key = api_key or settings.llm_api_key
        mdl = model or settings.llm_model
        self.researcher = Researcher(url, key, mdl)
        self.analyzer = Analyzer(url, key, mdl)
        self.writer = Writer(url, key, mdl)

    async def run(self, topic: str) -> dict:
        """Execute the full pipeline and return each stage's output."""
        research = await self.researcher.run(topic)
        analysis = await self.analyzer.run(
            f"Research findings:\n\n{research}\n\nAnalyze these findings."
        )
        output = await self.writer.run(
            f"Topic: {topic}\n\nResearch:\n{research}\n\nAnalysis:\n{analysis}\n\nWrite the final output."
        )
        return {
            "research": research,
            "analysis": analysis,
            "output": output,
        }
