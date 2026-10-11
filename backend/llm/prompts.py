"""提示词模板（Jinja2）。项目目录下的同名模板优先于内置模板。"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "prompts"
SPLIT = "=== user ==="


class PromptLibrary:
    def __init__(self, builtin_dir: Path = BUILTIN_DIR, override_dir: Path | None = None):
        self.builtin_dir = Path(builtin_dir).resolve()
        dirs = [str(d) for d in (override_dir, builtin_dir) if d is not None and Path(d).is_dir()]
        self.env = Environment(loader=FileSystemLoader(dirs), undefined=StrictUndefined,
                               autoescape=False, keep_trailing_newline=True)

    def source(self, name: str) -> tuple[str, bool]:
        """模板原文，以及是不是项目目录里的覆盖版（不是内置模板）。"""
        src, filename, _ = self.env.loader.get_source(self.env, f"{name}.md")
        builtin = Path(filename).resolve().parent == self.builtin_dir
        return src, not builtin

    def render(self, name: str, **ctx) -> tuple[str, str]:
        text = self.env.get_template(f"{name}.md").render(**ctx)
        if SPLIT not in text:
            raise ValueError(f"提示词模板 {name}.md 缺少分隔行 {SPLIT}")
        system, user = text.split(SPLIT, 1)
        return system.strip(), user.strip()
