"""提示词模板（Jinja2）。项目目录下的同名模板优先于内置模板。"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "prompts"
SPLIT = "=== user ==="


class PromptLibrary:
    def __init__(self, builtin_dir: Path = BUILTIN_DIR, override_dir: Path | None = None):
        dirs = [str(d) for d in (override_dir, builtin_dir) if d is not None and Path(d).is_dir()]
        self.env = Environment(loader=FileSystemLoader(dirs), undefined=StrictUndefined,
                               autoescape=False, keep_trailing_newline=True)

    def render(self, name: str, **ctx) -> tuple[str, str]:
        text = self.env.get_template(f"{name}.md").render(**ctx)
        if SPLIT not in text:
            raise ValueError(f"提示词模板 {name}.md 缺少分隔行 {SPLIT}")
        system, user = text.split(SPLIT, 1)
        return system.strip(), user.strip()
