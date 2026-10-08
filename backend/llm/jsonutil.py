"""从模型输出里抠出 JSON 对象。移植自 vglib/script.extract_json，另外去掉推理模型的 <think> 段。"""
from __future__ import annotations

import json
import re
from typing import Any

from backend.llm.base import LLMError

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def extract_json(raw: str) -> Any:
    text = _THINK.sub("", raw or "")
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        raise LLMError("模型没有返回 JSON。原始输出前 400 字：\n" + text[:400])
    blob = text[s:e + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_TRAILING_COMMA.sub(r"\1", blob))
    except json.JSONDecodeError as ex:
        raise LLMError(f"JSON 解析失败：{ex}\n原始输出前 600 字：\n{blob[:600]}") from ex
