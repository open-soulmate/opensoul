import json

import httpx

from src.config import settings


class ChainOfThought:
    """Step-by-step reasoning with self-reflection and correction."""

    SYSTEM_PROMPT = """You are a reasoning engine that thinks step by step.

Given a question and optional context, produce your reasoning as a JSON object:
{
  "understanding": "restate the question in your own words",
  "analysis": "break down the relevant information and context",
  "reasoning": "step-by-step logical deduction",
  "verification": "check your reasoning for errors or gaps",
  "answer": "your final answer",
  "confidence": 0.0 to 1.0
}

Rules:
- Be thorough in each step
- If confidence < 0.7, explain what is uncertain
- Return ONLY the JSON object, no other text"""

    REFLECTION_PROMPT = """Your previous answer had low confidence ({confidence}).
Previous reasoning: {reasoning}
Original question: {question}

Re-examine your reasoning carefully. Consider alternative interpretations or approaches.
Return the same JSON format with improved reasoning and confidence."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        self.base_url = base_url or settings.llm_base_url
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.max_reflections = 2

    async def think(self, question: str, context: str = "") -> dict:
        if not self.api_key:
            raise ValueError("LLM API key not configured")

        user_content = question
        if context:
            user_content = f"Context:\n{context}\n\nQuestion: {question}"

        result = await self._call_llm(self.SYSTEM_PROMPT, user_content)

        # Reflect if confidence is low
        reflections = 0
        while result.get("confidence", 0) < 0.7 and reflections < self.max_reflections:
            reflection_input = self.REFLECTION_PROMPT.format(
                confidence=result.get("confidence", 0),
                reasoning=result.get("reasoning", ""),
                question=question,
            )
            if context:
                reflection_input += f"\n\nContext:\n{context}"
            result = await self._call_llm(self.SYSTEM_PROMPT, reflection_input)
            reflections += 1

        result["reflections"] = reflections
        return result

    async def _call_llm(self, system_prompt: str, user_content: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2048,
                },
                timeout=120,
            )
            resp.raise_for_status()

        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(self._strip_code_fences(content))

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            first_newline = text.index("\n")
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()
