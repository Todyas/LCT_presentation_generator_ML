import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from app.core.agents.llm_client import LLMClient


class Dummy(BaseModel):
    a: int


def _fake_response(content: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class _FakeClient:
    """Records the exact kwargs passed to chat.completions.create."""

    def __init__(self, content: str | None) -> None:
        self.create = AsyncMock(return_value=_fake_response(content))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))


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


async def test_strips_fenceless_markdown_wrapper():
    with patch("app.core.agents.llm_client.AsyncOpenAI") as mock_openai_cls:
        mock_client = mock_openai_cls.return_value
        mock_client.chat.completions.create = AsyncMock(
            return_value=_fake_response('```\n{"a": 2}\n```')
        )

        llm = LLMClient(base_url="http://fake")
        result = await llm.complete_structured(
            model="qwen",
            system_prompt="sys",
            user_prompt="usr",
            response_model=Dummy,
            model_params={},
        )

        assert result.a == 2


async def test_system_prompt_embeds_json_schema_and_guided_json_matches():
    fake_client = _FakeClient('{"a": 1}')
    with patch("app.core.agents.llm_client.AsyncOpenAI", return_value=fake_client):
        llm = LLMClient(base_url="http://fake")
        await llm.complete_structured(
            model="qwen",
            system_prompt="sys",
            user_prompt="usr",
            response_model=Dummy,
            model_params={},
        )

    _, kwargs = fake_client.create.call_args
    schema = Dummy.model_json_schema()
    schema_json = json.dumps(schema, ensure_ascii=False)
    assert schema_json in kwargs["messages"][0]["content"]
    assert kwargs["extra_body"]["guided_json"] == schema


async def test_model_params_are_passed_through_to_the_completion_call():
    fake_client = _FakeClient('{"a": 1}')
    with patch("app.core.agents.llm_client.AsyncOpenAI", return_value=fake_client):
        llm = LLMClient(base_url="http://fake")
        await llm.complete_structured(
            model="qwen",
            system_prompt="sys",
            user_prompt="usr",
            response_model=Dummy,
            model_params={"temperature": 0.33, "max_tokens": 512},
        )

    _, kwargs = fake_client.create.call_args
    assert kwargs["temperature"] == 0.33
    assert kwargs["max_tokens"] == 512
    assert kwargs["model"] == "qwen"


async def test_none_content_raises_validation_error():
    with patch("app.core.agents.llm_client.AsyncOpenAI") as mock_openai_cls:
        mock_client = mock_openai_cls.return_value
        mock_client.chat.completions.create = AsyncMock(
            return_value=_fake_response(None)
        )

        llm = LLMClient(base_url="http://fake")
        with pytest.raises(ValidationError):
            await llm.complete_structured(
                model="qwen",
                system_prompt="sys",
                user_prompt="usr",
                response_model=Dummy,
                model_params={},
            )


async def test_invalid_json_raises_validation_error():
    with patch("app.core.agents.llm_client.AsyncOpenAI") as mock_openai_cls:
        mock_client = mock_openai_cls.return_value
        mock_client.chat.completions.create = AsyncMock(
            return_value=_fake_response("{not valid json")
        )

        llm = LLMClient(base_url="http://fake")
        with pytest.raises(ValidationError):
            await llm.complete_structured(
                model="qwen",
                system_prompt="sys",
                user_prompt="usr",
                response_model=Dummy,
                model_params={},
            )


async def test_extra_keys_are_silently_ignored_matching_pydantic_default():
    with patch("app.core.agents.llm_client.AsyncOpenAI") as mock_openai_cls:
        mock_client = mock_openai_cls.return_value
        mock_client.chat.completions.create = AsyncMock(
            return_value=_fake_response('{"a": 1, "unexpected_extra_field": "x"}')
        )

        llm = LLMClient(base_url="http://fake")
        result = await llm.complete_structured(
            model="qwen",
            system_prompt="sys",
            user_prompt="usr",
            response_model=Dummy,
            model_params={},
        )

        assert result.a == 1
        assert not hasattr(result, "unexpected_extra_field")


async def test_cyrillic_content_survives_schema_embedding():
    class WithCyrillic(BaseModel):
        title: str

    with patch("app.core.agents.llm_client.AsyncOpenAI") as mock_openai_cls:
        mock_client = mock_openai_cls.return_value
        mock_client.chat.completions.create = AsyncMock(
            return_value=_fake_response('{"title": "Рост выручки"}')
        )

        llm = LLMClient(base_url="http://fake")
        result = await llm.complete_structured(
            model="qwen",
            system_prompt="Заголовок должен быть на русском",
            user_prompt="usr",
            response_model=WithCyrillic,
            model_params={},
        )

        assert result.title == "Рост выручки"
        _, kwargs = mock_client.chat.completions.create.call_args
        # ensure_ascii=False means the embedded schema/system prompt keeps
        # literal Cyrillic instead of \uXXXX escapes.
        assert "Заголовок должен быть на русском" in kwargs["messages"][0]["content"]
        assert "\\u0420" not in kwargs["messages"][0]["content"]
