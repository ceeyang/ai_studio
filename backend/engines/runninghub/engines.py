"""RunningHub 云端引擎：把 ImageRequest / VideoRequest 按映射文件写成 nodeInfoList。

映射文件 workflows/runninghub_<引擎名>.map.yaml（或 config 里 map 指定的文件）：

    params:            # 我们的参数 → 云端工作流里的 [节点 id, 字段名]（可以写多个目标）
      prompt: [["6", "text"]]
      negative: [["7", "text"]]
      width: [["5", "width"]]
      height: [["5", "height"]]
      seed: [["3", "seed"]]
      first_frame: [["12", "image"]]     # 视频：首帧（自动上传）
      frames: [["15", "length"]]         # 视频：帧数（按 caps.fps 和秒数算）
      seconds: [["15", "duration"]]      # 视频：秒数（有的应用直接要秒数）
      refs: [["20", "image"], ["21", "image"]]   # 参考图，按顺序填，多出来的丢弃
    defaults:          # 每次都固定写入的值：[节点 id, 字段名, 值]
      steps: [["3", "steps", 20]]
    caps:              # 能力（都可省略）
      max_refs: 0
      supports_negative: true
      sizes: {"9:16": [720, 1280], "16:9": [1280, 720]}
      fps: 16             # 视频
      max_seconds: 5      # 视频
      has_audio: false    # 视频

参数没映射的就不写（云端工作流用它自己的默认值）。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import yaml

from backend.engines.base import (EngineCall, require_first_frame, ImageCaps, ImageRequest, VideoCaps, VideoRequest,
                                  size_for, upload_ref)
from backend.engines.runninghub.client import NO_KEY_HINT

log = logging.getLogger(__name__)
RUNTIME = "runninghub"
FAMILY = "runninghub"      # 队列给它开一条串行通道，不占本机 GPU worker
DEFAULT_SIZES = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (1024, 1024)}


@dataclass
class NodeMap:
    params: dict[str, list[tuple[str, str]]]
    fixed: list[tuple[str, str, object]]
    caps: dict

    @classmethod
    def load(cls, path: Path) -> "NodeMap":
        if not Path(path).exists():
            raise ValueError(f"RunningHub 映射文件不存在：{path}")
        m = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        params = {k: [(str(t[0]), str(t[1])) for t in v] for k, v in (m.get("params") or {}).items()}
        fixed = [(str(t[0]), str(t[1]), t[2]) for v in (m.get("defaults") or {}).values() for t in v]
        return cls(params, fixed, dict(m.get("caps") or {}))

    def node_info(self, values: dict) -> list[dict]:
        out = [{"nodeId": n, "fieldName": f, "fieldValue": v} for n, f, v in self.fixed]
        idx = {(d["nodeId"], d["fieldName"]): i for i, d in enumerate(out)}
        for key, targets in self.params.items():
            if key == "refs" or values.get(key) is None:
                continue
            for n, f in targets:
                item = {"nodeId": n, "fieldName": f, "fieldValue": values[key]}
                if (n, f) in idx:
                    out[idx[(n, f)]] = item
                else:
                    idx[(n, f)] = len(out)
                    out.append(item)
        for (n, f), ref in zip(self.params.get("refs") or [], values.get("refs") or []):
            out.append({"nodeId": n, "fieldName": f, "fieldValue": ref})
        return out


def _sizes(caps: dict) -> dict[str, tuple[int, int]]:
    raw = caps.get("sizes")
    return {k: (int(v[0]), int(v[1])) for k, v in raw.items()} if raw else dict(DEFAULT_SIZES)


class _RunningHubEngine:
    runtime = RUNTIME
    family = FAMILY

    def __init__(self, name: str, ecfg, nmap: NodeMap, rhcfg):
        if bool(ecfg.workflow_id) == bool(ecfg.webapp_id):
            raise ValueError(f"RunningHub 引擎 {name}：workflow_id 和 webapp_id 要写一个（只能写一个）")
        self.name = name
        self.label = ecfg.label or name
        self.cfg = ecfg
        self.rh = rhcfg
        self.map = nmap

    def preflight(self) -> str | None:
        """提交前检查：没有 API key 直接拒绝（不发请求）。"""
        if not self.rh.api_key():
            return f"{self.label}（RunningHub 云端）不能用：" + NO_KEY_HINT.format(env=self.rh.api_key_env)
        return None

    def _call(self, kind: str, values: dict, meta: dict, dropped: list[Path]) -> EngineCall:
        request = {"runner": RUNTIME, "workflow_id": self.cfg.workflow_id,
                   "webapp_id": self.cfg.webapp_id, "timeout": self.cfg.timeout,
                   "node_info_list": self.map.node_info(values)}
        return EngineCall(self.name, request, kind, self.family, meta=meta,
                          dropped_refs=[str(p) for p in dropped], runner="runninghub")


class RunningHubImageEngine(_RunningHubEngine):
    def __init__(self, name, ecfg, nmap, rhcfg):
        super().__init__(name, ecfg, nmap, rhcfg)
        c = nmap.caps
        max_refs = int(c.get("max_refs", len(nmap.params.get("refs") or [])))
        self.caps = ImageCaps(max_refs=max_refs, sizes=_sizes(c),
                              supports_negative=bool(c.get("supports_negative", "negative" in nmap.params)))

    def build(self, req: ImageRequest) -> EngineCall:
        w, h = size_for(self.caps.sizes, req.aspect)
        keep, dropped = req.refs[: self.caps.max_refs], req.refs[self.caps.max_refs:]
        values = {"prompt": req.prompt, "negative": req.negative or None, "width": w, "height": h,
                  "seed": req.seed, "aspect": req.aspect, "refs": [upload_ref(p) for p in keep]}
        return self._call("image", values,
                          {"prompt": req.prompt, "negative": req.negative, "seed": req.seed,
                           "size": [w, h], "refs": [str(p) for p in keep]}, dropped)


class RunningHubVideoEngine(_RunningHubEngine):
    def __init__(self, name, ecfg, nmap, rhcfg):
        super().__init__(name, ecfg, nmap, rhcfg)
        c = nmap.caps
        self.caps = VideoCaps(max_refs=int(c.get("max_refs", len(nmap.params.get("refs") or []))),
                              supports_end_frame="end_frame" in nmap.params,
                              has_audio=bool(c.get("has_audio", False)), fps=int(c.get("fps", 16)),
                              sizes=_sizes(c), default_seconds=float(c.get("default_seconds", 5.0)),
                              max_seconds=float(c.get("max_seconds", 5.0)))

    def frames_for(self, seconds: float) -> int:
        """默认按 Wan 系的 4k+1 帧。"""
        n = max(1, round(seconds * self.caps.fps))
        return max(17, (n // 4) * 4 + 1)

    def build(self, req: VideoRequest) -> EngineCall:
        require_first_frame(req, self)   # 没有首帧（参考图直出）而引擎不支持 ref_mode 时报清楚
        w, h = size_for(self.caps.sizes, req.aspect)
        seconds = min(req.seconds, self.caps.max_seconds)
        frames = self.frames_for(seconds)
        keep, dropped = req.refs[: self.caps.max_refs], req.refs[self.caps.max_refs:]
        values = {"prompt": req.prompt, "negative": req.negative or None, "width": w, "height": h,
                  "seed": req.seed, "aspect": req.aspect, "frames": frames,
                  "seconds": math.ceil(seconds), "fps": self.caps.fps,
                  "first_frame": upload_ref(req.first_frame),
                  "end_frame": upload_ref(req.end_frame) if req.end_frame else None,
                  "refs": [upload_ref(p) for p in keep]}
        return self._call("video", values,
                          {"prompt": req.prompt, "seed": req.seed, "size": [w, h], "frames": frames,
                           "fps": self.caps.fps, "first_frame": str(req.first_frame),
                           "refs": [str(p) for p in keep]}, dropped)


def load_runninghub_engines(cfg, reserved: set[str]) -> tuple[dict, dict[str, str]]:
    """按 config.runninghub.engines 建引擎；没配就是空。配错的引擎跳过（不让整个服务起不来），
    原因放在第二个返回值里给 doctor 报。"""
    rh = cfg.runninghub
    if rh is None:
        return {}, {}
    out, errors = {}, {}
    for name, ecfg in rh.engines.items():
        if not ecfg.enabled:
            continue
        try:
            if name in reserved:
                raise ValueError(f"RunningHub 引擎 {name} 和本机引擎重名，换个名字（比如 rh_{name}）")
            nmap = NodeMap.load(Path(cfg.workflows_dir) / (ecfg.map or f"runninghub_{name}.map.yaml"))
            cls = RunningHubImageEngine if ecfg.kind == "image" else RunningHubVideoEngine
            out[name] = cls(name, ecfg, nmap, rh)
        except Exception as e:  # noqa: BLE001  配置错误只影响这一个引擎
            errors[name] = str(e)
            log.warning("RunningHub 引擎 %s 配置有误，已跳过：%s", name, e)
    return out, errors
