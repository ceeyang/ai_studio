"""健康检查、引擎列表、配置。"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request

from backend.app.deps import services
from backend.doctor import run_doctor

router = APIRouter(prefix="/api", tags=["system"])
VERSION = "0.1.0"


@router.get("/health")
def health():
    return {"ok": True, "version": VERSION}


@router.get("/engines")
def engines(request: Request):
    reg = services(request).engines

    def row(name):
        e = reg.get(name)
        return {"name": name, "family": e.family, "caps": asdict(e.caps)}

    return {"image": [row(n) for n in reg.names("image")],
            "video": [row(n) for n in reg.names("video")]}


@router.get("/config")
def config(request: Request):
    # 配置里只有环境变量名，没有密钥值
    return request.app.state.cfg.model_dump(mode="json")


@router.get("/doctor")
def doctor(request: Request):
    sv = services(request)
    return [asdict(c) for c in run_doctor(sv.cfg, sv.engines, sv.comfy_client, sv.ffmpeg)]
