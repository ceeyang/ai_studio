"""设置页「默认模型」：默认文本模型 / 新项目的图片来源 / 新项目的视频来源 + 按步骤单独设置。

规则：设置页只管默认，项目和镜头上选的总是优先（见 backend/domain/settings.py）。
GET 返回每一项的设置值（null = 跟随默认）、现在实际生效的值、改回跟随默认后会用的值和可选项；
设置值已经失效（provider / 引擎不在了）时 invalid=true，生效值是退回去用的那个。
带 ?project=<slug> 时定妆照/三视图/场景图按这个项目算（工作台里引擎下拉框的「默认（…）」用）。
PUT 只改传了的字段，null = 跟随默认。改完立即生效，不用重启。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from backend.app.deps import project_by_slug, services
from backend.domain.settings import (GLOBAL_KEYS, STEP_KIND, STEP_LABEL, AppSettings,
                                     default_image_engine, default_video_engine, location_engine,
                                     portrait_engine,
                                     sheet_engine)
from backend.engines.registry import runtime_of
from backend.queue.errors import JobError

router = APIRouter(prefix="/api", tags=["settings"])

# 按步骤的 LLM 设置对应 config.yaml 里 llm.routes 的哪个任务（角色相关的三步共用 character）
CONFIG_TASK = {"character.suggest": "character", "character.recommend": "character",
               "character.turnaround_look": "character"}


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")      # 拼错字段名直接 422，不悄悄忽略

    text_model: str | None = None
    image_engine: str | None = None
    video_engine: str | None = None
    steps: dict[str, str | None] | None = None


def _engine_rows(sv, kind: str) -> list[dict]:
    rows = []
    for name in sv.engines.names(kind):
        e = sv.engines.get(name)
        pre = getattr(e, "preflight", None)
        note = pre() if callable(pre) else None
        rows.append({"name": name, "label": getattr(e, "label", None) or name,
                     "runtime": runtime_of(e), "family": e.family, "enabled": True,
                     "ready": note is None, "note": note})
    return rows


def _config_provider(cfg, task: str) -> str | None:
    try:
        return cfg.llm.provider_for(CONFIG_TASK.get(task, task)).id
    except KeyError:
        return None


def build_settings_out(sv, st: AppSettings, project=None) -> dict[str, Any]:
    """每一项给 value（设置值）、effective（现在生效的）、fallback（改回「跟随默认」后会用的）、
    invalid（设置值已经不在可选项里）。"""
    cfg = sv.cfg
    providers = {p.id: p for p in cfg.llm.providers}
    image_names, video_names = set(sv.engines.names("image")), set(sv.engines.names("video"))

    def name_of(pid):
        return providers[pid].display_name() if pid in providers else pid

    def llm_row(value, fallback, fallback_source):
        ok = value in providers
        eff = value if ok else fallback
        return {"value": value, "effective": eff, "effective_label": name_of(eff),
                "fallback": fallback, "fallback_label": name_of(fallback),
                "invalid": bool(value) and not ok,
                "source": "settings" if value and ok else fallback_source}

    bare = AppSettings(steps={})

    def without(key):
        return replace(st, steps={k: v for k, v in st.steps.items() if k != key})

    proj_image = project.image_engine if project is not None else default_image_engine(sv, st=st)

    def image_eff(key, x):
        if key == "portrait":
            return portrait_engine(sv, proj_image, st=x)
        if key == "location":
            return location_engine(sv, proj_image, st=x)
        return sheet_engine(sv, proj_image, st=x)

    steps = []
    for key, label in STEP_LABEL.items():
        kind = STEP_KIND[key]
        value = st.step(key)
        if kind == "llm":
            fb = st.text_model if st.text_model in providers else _config_provider(cfg, key)
            row = llm_row(value, fb, "settings" if fb == st.text_model and st.text_model else "config")
            if value and not row["invalid"]:
                row["source"] = "step"
        else:
            eff, fb = image_eff(key, st), image_eff(key, without(key))
            row = {"value": value, "effective": eff, "fallback": fb,
                   "invalid": bool(value) and value not in image_names,
                   "source": "step" if value and value == eff else "default"}
        steps.append({"key": key, "label": label, "kind": kind, **row})

    def engine_row(value, names, effective, fallback):
        return {"value": value, "effective": effective, "fallback": fallback,
                "invalid": bool(value) and value not in names,
                "source": "settings" if value and value == effective else "default"}

    cfg_default = cfg.llm.default if cfg.llm.default in providers else None
    return {
        "text_model": llm_row(st.text_model, cfg_default, "config"),
        "image_engine": engine_row(st.image_engine, image_names, default_image_engine(sv, st=st),
                                   default_image_engine(sv, st=bare)),
        "video_engine": engine_row(st.video_engine, video_names, default_video_engine(sv, st=st),
                                   default_video_engine(sv, st=bare)),
        "steps": steps,
        "project": project.slug if project is not None else None,
        "choices": {
            "llm": [{"id": p.id, "label": p.display_name(), "kind": p.kind, "model": p.model}
                    for p in cfg.llm.providers],
            "image": _engine_rows(sv, "image"),
            "video": _engine_rows(sv, "video"),
        },
    }


def _out(sv, project_slug: str | None) -> dict[str, Any]:
    st = sv.settings.load()
    if not project_slug:
        return build_settings_out(sv, st)
    with sv.db.session() as s:
        return build_settings_out(sv, st, project_by_slug(s, project_slug))


@router.get("/settings")
def get_settings(request: Request, project: str | None = None):
    return _out(services(request), project)


def _check(sv, kind: str, value: str | None, what: str) -> None:
    if value is None:
        return
    if kind == "llm":
        ids = [p.id for p in sv.cfg.llm.providers]
        if value not in ids:
            raise JobError(f"{what}：文本模型 {value} 不在 config.yaml 的 llm.providers 里，可选：{', '.join(ids)}")
        return
    names = sv.engines.names(kind)
    if value not in names:
        label = "图片来源" if kind == "image" else "视频来源"
        raise JobError(f"{what}：{label} {value} 不存在或未启用，可选：{', '.join(names)}")


@router.put("/settings")
def put_settings(body: SettingsIn, request: Request):
    sv = services(request)
    data = body.model_dump(exclude_unset=True)
    values: dict[str, Any] = {}
    names = {"text_model": "默认文本模型", "image_engine": "新项目的图片来源",
             "video_engine": "新项目的视频来源"}
    for key, kind in GLOBAL_KEYS.items():
        if key in data:
            v = data[key] or None
            _check(sv, kind, v, names[key])
            values[key] = v
    for step, v in (data.get("steps") or {}).items():
        if step not in STEP_KIND:
            raise JobError(f"没有这个步骤：{step}，可选：{', '.join(STEP_KIND)}")
        v = v or None
        _check(sv, STEP_KIND[step], v, STEP_LABEL[step])
        values[f"step:{step}"] = v
    sv.settings.save(values)
    return _out(sv, None)

