"""所有路由。新增路由模块时加到 ROUTERS。"""
from backend.app.routes import candidates, characters, episodes, jobs, projects, runs, shots, system, ws

ROUTERS = [projects.router, characters.router, episodes.router, shots.router, runs.router, candidates.router,
           jobs.router, system.router, ws.router]
