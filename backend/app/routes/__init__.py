"""所有路由。新增路由模块时加到 ROUTERS。"""
from backend.app.routes import browser_ext, candidates, characters, episodes, jobs, locations, projects, runs, settings, shots, system, ws

ROUTERS = [projects.router, characters.router, locations.router, episodes.router, shots.router, runs.router, candidates.router,
           jobs.router, system.router, settings.router, ws.router, browser_ext.router]
