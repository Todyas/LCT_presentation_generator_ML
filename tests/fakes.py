"""Test doubles shared across the suite."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

ScriptValue = dict | str | BaseModel | BaseException
ScriptEntry = ScriptValue | Callable[[str], ScriptValue]


@dataclass
class CallRecord:
    response_model: type[BaseModel]
    system_prompt: str
    user_prompt: str
    model_params: dict[str, Any]


class ScriptedLLM:
    """Drop-in replacement for ``LLMClient`` with responses scripted per ``response_model``.

    Script entries are consumed in order; once a model's queue is down to one
    entry, that entry repeats forever (so tests do not need to know exactly
    how many retries will happen). Every payload is round-tripped through
    ``response_model.model_validate[_json]`` so invalid scripted payloads
    raise a real ``pydantic.ValidationError``, exactly like production.

    A script entry may also be a callable ``(user_prompt) -> value``; it is
    invoked fresh on every call, and if it returns an awaitable (e.g. it is
    itself an ``async def``), that awaitable is awaited first. This is how
    tests simulate two concurrent LLM calls that need to interleave at
    specific points (gate one on an ``asyncio.Event`` the other sets).
    """

    def __init__(self) -> None:
        self._scripts: dict[str, list[ScriptEntry]] = {}
        self.calls: list[CallRecord] = []

    def script(
        self, response_model: type[BaseModel], *entries: ScriptEntry
    ) -> ScriptedLLM:
        self._scripts.setdefault(response_model.__name__, []).extend(entries)
        return self

    def replace(
        self, response_model: type[BaseModel], *entries: ScriptEntry
    ) -> ScriptedLLM:
        """Like `script`, but discards any entries already queued for this
        response_model instead of appending after them — useful mid-test,
        when a later call must switch behaviour starting on its very next
        invocation rather than after the current queue drains."""
        self._scripts[response_model.__name__] = list(entries)
        return self

    async def complete_structured(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
        model_params: dict[str, Any],
    ) -> BaseModel:
        self.calls.append(
            CallRecord(
                response_model=response_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model_params=dict(model_params),
            )
        )
        key = response_model.__name__
        queue = self._scripts.get(key)
        if not queue:
            raise AssertionError(f"ScriptedLLM: no script entries left for {key}")
        entry = queue[0]
        if len(queue) > 1:
            queue.pop(0)

        if callable(entry) and not isinstance(
            entry, (dict, str, BaseModel, BaseException)
        ):
            entry = entry(user_prompt)
            if inspect.isawaitable(entry):
                entry = await entry

        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, str):
            return response_model.model_validate_json(entry)
        if isinstance(entry, BaseModel):
            return response_model.model_validate(entry.model_dump(mode="json"))
        return response_model.model_validate(entry)

    def remaining(self, response_model: type[BaseModel]) -> int:
        return len(self._scripts.get(response_model.__name__, []))
