from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel

from app.core.agents.llm_client import LLMClient


class Dummy(BaseModel):
    a: int


def _fake_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


async def test_strips_markdown_fence_before_validation():
    with patch("app.core.agents.llm_client.AsyncOpenAI") as mock_openai_cls:
        mock_client = mock_openai_cls.return_value
        mock_client.chat.completions.create = AsyncMock(
            return_value=_fake_response('```json\n{"a": 1}\n```')
        )

        llm = LLMClient(base_url="http://fake")
        result = await llm.complete_structured(
            model="qwen",
            system_prompt="sys",
            user_prompt="usr",
            response_model=Dummy,
            model_params={"temperature": 0.1},
        )

        assert result.a == 1
