"""真实环境自检：uv run python -m backend.llm.selftest <provider_id> [--no-sdk]"""
from __future__ import annotations

import sys

from pydantic import BaseModel

from backend.config import load_config
from backend.llm.gateway import LLMGateway
from backend.llm.providers import build_providers


class Pong(BaseModel):
    ok: bool
    word: str


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("用法：python -m backend.llm.selftest <provider_id> [--no-sdk]")
        return 2
    cfg = load_config()
    pcfg = cfg.llm.provider(argv[0])
    if "--no-sdk" in argv:
        pcfg.use_sdk = False
    cfg.llm.routes["selftest"] = pcfg.id
    gw = LLMGateway(cfg.llm, build_providers(cfg.llm))
    out = gw.generate("selftest", "你是接口自检程序，只输出 JSON。",
                      '输出 {"ok": true, "word": "你好"}，不要任何其他文字。', Pong, log=print)
    print(f"通过：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
