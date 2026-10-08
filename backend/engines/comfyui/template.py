"""ComfyUI 工作流模板：workflows/<name>.json（API 格式）+ <name>.map.yaml（参数 → 节点输入）。

在 ComfyUI 界面里调好的图，导出 API 格式，再写一份 map，就能接进来。
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class WorkflowTemplate:
    name: str
    workflow: dict
    params: dict[str, list[tuple[str, str]]]
    defaults: dict
    model_params: list[str]

    @classmethod
    def load(cls, workflows_dir: Path, name: str) -> "WorkflowTemplate":
        wf_path = Path(workflows_dir) / f"{name}.json"
        map_path = Path(workflows_dir) / f"{name}.map.yaml"
        workflow = json.loads(wf_path.read_text(encoding="utf-8"))
        m = yaml.safe_load(map_path.read_text(encoding="utf-8")) or {}
        params = {k: [(str(n), str(i)) for n, i in v] for k, v in (m.get("params") or {}).items()}
        for key, targets in params.items():
            for node, inp in targets:
                if node not in workflow:
                    raise ValueError(f"{map_path.name}：参数 {key} 指向不存在的节点 {node}")
                if inp not in (workflow[node].get("inputs") or {}):
                    raise ValueError(f"{map_path.name}：节点 {node} 没有输入 {inp}")
        return cls(name, workflow, params, dict(m.get("defaults") or {}),
                   list(m.get("models") or []))

    def class_types(self) -> set[str]:
        return {n["class_type"] for n in self.workflow.values()}

    def model_files(self, overrides: dict | None = None) -> list[str]:
        vals = {**self.defaults, **(overrides or {})}
        return [str(vals[k]) for k in self.model_params if vals.get(k)]

    def render(self, values: dict) -> dict:
        merged = {**self.defaults, **{k: v for k, v in values.items() if v is not None}}
        wf = copy.deepcopy(self.workflow)
        for key, targets in self.params.items():
            if key not in merged:
                raise KeyError(f"工作流模板 {self.name} 缺少参数 {key}")
            for node, inp in targets:
                wf[node]["inputs"][inp] = merged[key]
        return wf
