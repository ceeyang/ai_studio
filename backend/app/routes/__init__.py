"""所有路由。新增路由模块时加到 ROUTERS。"""
from backend.app.routes import episodes, projects, shots

ROUTERS = [projects.router, episodes.router, shots.router]
