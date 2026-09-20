from __future__ import annotations

import json
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
        # `guided_json` only constrains decoding on vLLM/TGI. Other OpenAI-compatible
        # backends (OpenRouter, hosted APIs, etc.) silently ignore unknown extra_body
        # keys, so the schema must also be spelled out in-prompt or the model has no
        # way to know the required field names at all.
        schema_json = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        augmented_system_prompt = (
            f"{system_prompt}\n\n"
            "Your entire reply must be a single JSON object with no surrounding text, "
            "markdown fences, or commentary, and it must validate against this JSON "
            f"Schema exactly (required keys, exact key names, correct types):\n{schema_json}"
        )

        response = await self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": augmented_system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            extra_body={"guided_json": response_model.model_json_schema()},
            **model_params,
        )
        content = response.choices[0].message.content or ""
        content = _FENCE_RE.sub("", content).strip()
        return response_model.model_validate_json(content)
