"""ComfyUI 错误。连接类可重试；工作流校验、模型缺失、执行报错不可重试，并带出错节点。"""
from __future__ import annotations

import httpx


class ComfyError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False,
                 node_id: str | None = None, node_type: str | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.node_id = node_id
        self.node_type = node_type


def error_from_response(path: str, r: httpx.Response) -> ComfyError:
    try:
        body = r.json()
    except ValueError:
        return ComfyError(f"ComfyUI {path} 返回 HTTP {r.status_code}：{r.text[:500]}")
    for nid, ne in (body.get("node_errors") or {}).items():
        e0 = (ne.get("errors") or [{}])[0]
        detail = e0.get("details") or ""
        msg = f"ComfyUI 拒绝工作流：节点 {nid}（{ne.get('class_type', '?')}）{e0.get('message', '')}"
        if detail:
            msg += f"：{detail}"
        return ComfyError(msg, node_id=str(nid), node_type=ne.get("class_type"))
    err = body.get("error")
    if isinstance(err, dict):
        msg = err.get("message") or str(body)[:500]
        if err.get("details"):
            msg += f"：{err['details']}"
    else:
        msg = str(err or body)[:500]
    return ComfyError(f"ComfyUI {path} 返回 HTTP {r.status_code}：{msg}")


def execution_error(entry: dict) -> ComfyError | None:
    status = entry.get("status") or {}
    for m in status.get("messages") or []:
        if isinstance(m, list) and len(m) == 2 and m[0] == "execution_error":
            d = m[1] or {}
            return ComfyError(
                f"ComfyUI 执行失败：节点 {d.get('node_id')}（{d.get('node_type')}）"
                f"{str(d.get('exception_message', '')).strip()}",
                node_id=str(d.get("node_id")), node_type=d.get("node_type"))
    if status.get("status_str") == "error":
        return ComfyError("ComfyUI 执行失败（没有返回详细信息，看 ComfyUI 控制台）")
    return None
