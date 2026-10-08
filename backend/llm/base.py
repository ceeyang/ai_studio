"""LLM provider 的公共类型。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResult:
    text: str
    model: str | None = None
    usage: dict | None = None
    seconds: float = 0.0


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    id: str

    def complete(self, system: str, messages: list[dict], *, json_mode: bool = True) -> LLMResult: ...
