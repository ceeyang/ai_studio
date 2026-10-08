"""FastAPI 入口：uv run studio-server"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

import backend.pipeline  # noqa: F401  注册各阶段处理器
from backend.app.deps import install_error_handlers, is_local_origin
from backend.app.routes import ROUTERS
from backend.config import Config, load_config
from backend.domain.db import Database
from backend.logging_setup import setup_logging
from backend.queue.events import EventBus
from backend.queue.handlers import HANDLERS
from backend.queue.manager import JobManager
from backend.services import build_services

ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]  # testserver 是 TestClient 的默认 Host
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def install_local_guards(app: FastAPI) -> None:
    """只服务本机：Host 不是本机名的请求一律 400（防 DNS rebinding）；
    带了 Origin 且不是本机页面的写请求一律 403（防别的网站在浏览器里跨站提交）。"""

    @app.middleware("http")
    async def reject_cross_origin_writes(request: Request, call_next):
        origin = request.headers.get("origin")
        if (request.method not in SAFE_METHODS and origin is not None
                and not is_local_origin(origin)):
            return JSONResponse({"detail": f"拒绝来自 {origin} 的跨站写请求"}, status_code=403)
        return await call_next(request)

    # 后加的中间件在最外层：先查 Host，再查 Origin
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


def create_app(cfg: Config | None = None, *, start_workers: bool = True,
               configure_logging: bool = True) -> FastAPI:
    cfg = cfg or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if configure_logging:
            setup_logging(cfg.log_dir)
        db = Database(cfg.data_dir / "studio.db")
        db.upgrade()
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        manager = JobManager(db, HANDLERS, bus)
        services = build_services(cfg, db, manager)
        manager.services = services
        manager.recover()
        app.state.cfg, app.state.services = cfg, services
        app.state.manager, app.state.bus = manager, bus
        if start_workers:
            manager.start()
        try:
            yield
        finally:
            manager.stop()
            db.engine.dispose()

    app = FastAPI(title="AI Studio", lifespan=lifespan)
    install_error_handlers(app)
    install_local_guards(app)
    for r in ROUTERS:
        app.include_router(r)
    return app


def run() -> None:
    cfg = load_config()
    uvicorn.run(create_app(cfg), host=cfg.server.host, port=cfg.server.port, log_level="info")
