import json

import httpx

from src.config import settings


EXTRACTION_PROMPT = """Extract entities and relations from the following text.
Return a JSON object with two keys:
- "entities": list of {"name": str, "type": str, "description": str}
- "relations": list of {"source": str, "target": str, "relation_type": str}

Entity types: person, place, concept, event, organization, technology, other

Text:
{text}

Return only valid JSON, no markdown fences."""


async def extract_entities_and_relations(text: str) -> dict:
    """Use LLM to extract entities and relations from text."""
    api_key = settings.llm_api_key
    if not api_key:
        return {"entities": [], "relations": []}

    prompt = EXTRACTION_PROMPT.format(text=text[:4000])
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)
