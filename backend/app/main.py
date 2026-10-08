"""FastAPI 入口：uv run studio-server"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

import backend.pipeline  # noqa: F401  注册各阶段处理器
from backend.app.deps import install_error_handlers
from backend.app.routes import ROUTERS
from backend.config import Config, load_config
from backend.domain.db import Database
from backend.logging_setup import setup_logging
from backend.queue.events import EventBus
from backend.queue.handlers import HANDLERS
from backend.queue.manager import JobManager
from backend.services import build_services


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
    for r in ROUTERS:
        app.include_router(r)
    return app


def run() -> None:
    cfg = load_config()
    uvicorn.run(create_app(cfg), host=cfg.server.host, port=cfg.server.port, log_level="info")
