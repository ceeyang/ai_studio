"""所有路由。新增路由模块时加到 ROUTERS。"""
from backend.app.routes import api_services, assets, browser_ext, candidates, characters, episodes, jobs, locations, projects, props, runs, settings, shots, system, ws

ROUTERS = [projects.router, characters.router, locations.router, props.router, episodes.router, shots.router,
           assets.router, runs.router, candidates.router,
           jobs.router, system.router, settings.router, api_services.router, ws.router,
           browser_ext.router]
