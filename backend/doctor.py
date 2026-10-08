"""环境检查：ComfyUI、各引擎的节点与模型、LLM、ffmpeg、磁盘、内存。只报告，不改状态。"""
from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
import psutil

from backend.config import Config
from backend.engines.comfyui.errors import ComfyError


@dataclass
class Check:
    group: str
    name: str
    status: Literal["ok", "warn", "fail"]
    detail: str = ""


def _http_get(url: str) -> Any:
    r = httpx.get(url, timeout=3, trust_env=False)
    r.raise_for_status()
    return r.json()


def _model_index(models_dir: Path | None) -> set[str] | None:
    if not models_dir or not Path(models_dir).is_dir():
        return None
    return {p.name.lower() for p in Path(models_dir).rglob("*") if p.is_file()}


def _basename(f: str) -> str:
    return f.replace("\\", "/").rsplit("/", 1)[-1].lower()


def run_doctor(cfg: Config, engines, comfy, ffmpeg, *,
               http_get: Callable[[str], Any] = _http_get,
               which: Callable[[str], str | None] = shutil.which) -> list[Check]:
    out: list[Check] = []

    info = None
    if comfy is None:
        out.append(Check("ComfyUI", "服务", "fail", "未配置"))
    else:
        try:
            st = comfy.system_stats(tries=1, timeout=3)
            dev = (st.get("devices") or [{}])[0]
            out.append(Check("ComfyUI", "服务", "ok",
                             f"{cfg.comfyui.url} · {dev.get('name', '?')} · 显存空闲 "
                             f"{dev.get('vram_free', 0) / 2**30:.1f}/{dev.get('vram_total', 0) / 2**30:.1f} GB"))
        except ComfyError as e:
            out.append(Check("ComfyUI", "服务", "fail", str(e)))
        else:
            try:
                info = comfy.object_info(tries=1, timeout=15)
            except ComfyError as e:
                out.append(Check("ComfyUI", "节点信息", "fail", str(e)))

    index = _model_index(cfg.comfyui.models_dir)
    if index is None:
        out.append(Check("引擎", "模型目录", "warn",
                         "没配置 comfyui.models_dir 或目录不存在，跳过模型文件检查"))
    for name, eng in sorted(engines.all().items()):
        if info is not None:
            types = eng.tpl.class_types()
            missing = sorted(types - set(info))
            out.append(Check("引擎", f"{name} 节点", "fail" if missing else "ok",
                             "缺少节点：" + "、".join(missing) if missing else f"{len(types)} 个节点都在"))
        if index is not None:
            files = eng.tpl.model_files(eng.overrides)
            miss = [f for f in files if _basename(f) not in index]
            out.append(Check("引擎", f"{name} 模型", "fail" if miss else "ok",
                             "缺少：" + "、".join(miss) if miss else f"{len(files)} 个模型文件都在"))

    for p in cfg.llm.providers:
        if p.kind == "openai":
            try:
                d = http_get(p.base_url.rstrip("/") + "/models")
                n = len(d.get("data") or [])
                out.append(Check("LLM", p.id, "ok", f"{p.base_url} · {n} 个模型"))
            except Exception as e:
                out.append(Check("LLM", p.id, "warn",
                                 f"连不上 {p.base_url}（{type(e).__name__}）。本机 LM Studio 平时不开，用到前先启动"))
        elif p.kind == "claude":
            exe = which("claude")
            out.append(Check("LLM", p.id, "ok" if exe else "fail", exe or "找不到 claude 命令"))
        else:
            out.append(Check("LLM", p.id, "ok", "手动粘贴结果"))

    try:
        v = ffmpeg.version()
        first = (v.splitlines() or ["ffmpeg"])[0]
        if "--enable-libass" in v:
            out.append(Check("工具", "ffmpeg", "ok", first))
        else:
            out.append(Check("工具", "ffmpeg", "warn", first + "（没有 libass，烧字幕会失败）"))
    except Exception as e:
        out.append(Check("工具", "ffmpeg", "fail", str(e)))

    probe = Path(cfg.data_dir).resolve()
    while not probe.exists() and probe != probe.parent:  # 数据目录还没建时看它所在的盘
        probe = probe.parent
    free = shutil.disk_usage(probe).free / 2**30
    out.append(Check("系统", "磁盘", "ok" if free >= 50 else "warn",
                     f"{cfg.data_dir} 所在盘剩余 {free:.0f} GB"))
    mem = psutil.virtual_memory()
    avail = mem.available / 2**30
    out.append(Check("系统", "内存", "ok" if avail >= 8 else "warn",
                     f"可用 {avail:.1f} / {mem.total / 2**30:.0f} GB"))
    return out
