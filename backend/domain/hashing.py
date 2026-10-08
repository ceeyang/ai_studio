"""产物的输入指纹。输入变了，指纹就变，旧候选即为过期。"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def input_hash(*parts: Any, **named: Any) -> str:
    blob = json.dumps({"p": parts, "n": named}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
