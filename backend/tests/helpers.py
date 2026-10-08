"""测试用的假对象。"""
from __future__ import annotations

import json

from backend.llm.base import LLMResult


class FakeProvider:
    def __init__(self, texts, provider_id: str = "fake"):
        self.id = provider_id
        self.texts = list(texts)
        self.calls: list[dict] = []

    def complete(self, system, messages, *, json_mode=True):
        self.calls.append({"system": system, "messages": list(messages)})
        if not self.texts:
            raise AssertionError("FakeProvider 没有更多预置回复")
        t = self.texts.pop(0)
        if not isinstance(t, str):
            t = json.dumps(t, ensure_ascii=False)
        return LLMResult(text=t, model="fake", usage=None, seconds=0.0)
