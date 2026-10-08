"""把 API 格式工作流转成 ComfyUI 界面可导入的图形格式。移植自 ai_videos/vglib/export.py（自有代码）。

runs/<job_id>/workflow.api.json 里存的是 API 格式 {节点id: {class_type, inputs}}，那是 /prompt
接口吃的；界面「导入」要的是 {nodes:[...], links:[...]}，必须显式声明每个节点的输入插槽、
输出插槽、连线和 widget 取值顺序——这些信息从 /object_info 取。

三个容易踩的点：
  1. COMBO 在 object_info 里可能是字符串类型名而不是选项列表，也得算 widget
  2. 种子类 widget 界面会多挂一个 control_after_generate，LoadImage 多一个上传按钮
  3. autogrow 输入（如 ref_images）实际插槽名是带点的 ref_images.ref_image_0
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN"}
AUTOGROW = "AUTOGROW"
COLUMN_WIDTH = 420
ROW_HEIGHT = 200


class MissingNodeError(ValueError):
    """object_info 里没有工作流用到的节点类型（ComfyUI 没装对应的自定义节点）。"""


def info_lookup(object_info: dict) -> Callable[[str], dict]:
    def get(class_type: str) -> dict:
        try:
            return object_info[class_type]
        except KeyError:
            raise MissingNodeError(
                f"ComfyUI 里没有节点 {class_type}，装好对应的自定义节点后再导出") from None
    return get


def _opts(spec: list) -> dict:
    return spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}


def _tname(spec: list) -> str:
    return "*" if isinstance(spec[0], list) else str(spec[0])


def _is_widget(spec: list) -> bool:
    t = spec[0]
    if isinstance(t, list):
        return True
    t = str(t)
    return t in WIDGET_TYPES or "COMBO" in t


def _is_autogrow(spec: list) -> bool:
    return AUTOGROW in _tname(spec)


def _is_link(v: Any) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[1], int)


def _default(spec: list) -> Any:
    t, o = spec[0], _opts(spec)
    if "default" in o:
        return o["default"]
    if isinstance(t, list):
        return t[0] if t else ""
    return {"INT": 0, "FLOAT": 0.0, "STRING": "", "BOOLEAN": False}.get(str(t), "")


def to_ui_graph(api_wf: dict, get_info: Callable[[str], dict]) -> dict:
    ids = {k: i + 1 for i, k in enumerate(api_wf)}
    info = {k: get_info(v["class_type"]) for k, v in api_wf.items()}

    widgets: dict[str, list] = {}
    slots: dict[str, list[tuple[str, str | None, Any]]] = {}
    for k, node in api_wf.items():
        d = info[k].get("input") or {}
        decl = list((d.get("required") or {}).items()) + list((d.get("optional") or {}).items())
        wv: list = []
        sl: list[tuple[str, str | None, Any]] = []
        seen: set[str] = set()
        for name, spec in decl:
            seen.add(name)
            val = node["inputs"].get(name)
            if _is_autogrow(spec):
                continue
            if _is_widget(spec) and not _is_link(val):
                wv.append(_default(spec) if val is None else val)
                o = _opts(spec)
                if o.get("control_after_generate"):
                    wv.append("fixed")
                if o.get("image_upload"):
                    wv.append("image")
            else:
                sl.append((name, _tname(spec), val))
        for name, val in node["inputs"].items():
            if name not in seen and _is_link(val):
                sl.append((name, None, val))
        widgets[k], slots[k] = wv, sl

    links: list[list] = []
    link_id = 0
    inbound: dict[str, list[dict]] = {}
    outbound: dict[str, dict[int, list[int]]] = {k: {} for k in api_wf}
    for k in api_wf:
        ins: list[dict] = []
        for name, t, val in slots[k]:
            if _is_link(val) and val[0] in api_wf:
                src, sslot = val[0], int(val[1])
                if t is None:
                    outs = info[src].get("output") or []
                    t = outs[sslot] if sslot < len(outs) else "*"
                link_id += 1
                links.append([link_id, ids[src], sslot, ids[k], len(ins), t])
                outbound[src].setdefault(sslot, []).append(link_id)
                ins.append({"name": name, "type": t, "link": link_id})
            else:
                ins.append({"name": name, "type": t or "*", "link": None})
        inbound[k] = ins

    rid = {v: k for k, v in ids.items()}
    depth: dict[str, int] = {}

    def dep(k: str, seen: tuple = ()) -> int:
        if k in depth:
            return depth[k]
        if k in seen:
            return 0
        ups = [rid[ln[1]] for ln in links if ln[3] == ids[k]]
        depth[k] = 0 if not ups else 1 + max(dep(u, seen + (k,)) for u in ups)
        return depth[k]

    for k in api_wf:
        dep(k)
    col: dict[int, int] = {}
    pos: dict[str, list[int]] = {}
    for k in sorted(api_wf, key=lambda x: (depth[x], ids[x])):
        c = depth[k]
        r = col.get(c, 0)
        col[c] = r + 1
        pos[k] = [c * COLUMN_WIDTH, r * ROW_HEIGHT]

    nodes = []
    for k, node in api_wf.items():
        d = info[k]
        names = d.get("output_name") or []
        outs = []
        for i, t in enumerate(d.get("output") or []):
            outs.append({"name": names[i] if i < len(names) else t, "type": t,
                         "links": outbound[k].get(i, []), "slot_index": i})
        nodes.append({
            "id": ids[k], "type": node["class_type"], "pos": pos[k],
            "size": [360, 30 + 26 * max(1, len(widgets[k]) + len(inbound[k]))],
            "flags": {}, "order": depth[k], "mode": 0,
            "inputs": inbound[k], "outputs": outs,
            "properties": {"Node name for S&R": node["class_type"]},
            "widgets_values": widgets[k],
        })

    # id 必须是字符串 UUID：界面的 schema 是 z.string().optional()，给 null 会校验失败，
    # 报「无法找到工作流」
    return {"id": str(uuid.uuid4()), "revision": 0,
            "last_node_id": max(ids.values()) if ids else 0,
            "last_link_id": link_id, "nodes": nodes, "links": links,
            "groups": [], "config": {},
            "extra": {"ds": {"scale": 0.75, "offset": [80, 80]}},
            "version": 0.4}
