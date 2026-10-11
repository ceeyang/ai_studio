"""导入各阶段模块，让它们把处理器登记到 HANDLERS。"""
from backend.pipeline import (characters, continuity, export, fit_khmer, keyframes,  # noqa: F401
                              locations, post, props, script, storyboard, videos)
