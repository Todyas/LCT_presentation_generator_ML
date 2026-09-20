from __future__ import annotations

import re

from openai import AsyncOpenAI
from pydantic import BaseModel

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMClient:
    def __init__(self, base_url: str, api_key: str = "EMPTY") -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def complete_structured[T: BaseModel](
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        model_params: dict,
    ) -> T:
        response = await self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            extra_body={"guided_json": response_model.model_json_schema()},
            **model_params,
        )
        content = response.choices[0].message.content or ""
        content = _FENCE_RE.sub("", content).strip()
        return response_model.model_validate_json(content)
