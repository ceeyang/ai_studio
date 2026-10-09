"""导入各阶段模块，让它们把处理器登记到 HANDLERS。"""
from backend.pipeline import (characters, export, fit_khmer, keyframes, post, script,  # noqa: F401
                              storyboard, videos)
