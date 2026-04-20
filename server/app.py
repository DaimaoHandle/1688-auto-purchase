"""
FastAPI 应用工厂。
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import os

from server.db.database import init_db
from server.ws.agent_handler import agent_ws_endpoint
from server.api.nodes import router as nodes_router
from server.api.tasks import router as tasks_router
from server.api.configs import router as configs_router
from server.api.images import router as images_router
from server.api.reports import router as reports_router
from server.api.auth import router as auth_router, get_current_user
from server.api.users import router as users_router
from server.api.audit import router as audit_router
from server.api.purchased import router as purchased_router
from server.api.logs import router as logs_router
from server.services.node_manager import node_manager
from shared.protocol import parse_message, make_message, MSG_STATUS_UPDATE

logger = logging.getLogger("server")

# 不需要登录的路径
PUBLIC_PATHS = {"/api/auth/login", "/ws/agent", "/ws/dashboard", "/static", "/"}
PUBLIC_PREFIXES = ("/api/auth/login", "/ws/", "/static/", "/api/images/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("数据库已初始化（默认管理员 admin/admin）")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="1688 采购管理系统", lifespan=lifespan)

    # 认证中间件：除公开路径外，所有 API 请求需登录
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        # 公开路径放行
        if path == "/" or path.startswith("/static/") or path.startswith("/ws/"):
            return await call_next(request)
        if path == "/api/auth/login":
            return await call_next(request)
        # Agent 相关 API 放行（图片、采购历史、日志）
        for public_prefix in ["/api/images", "/api/purchased", "/api/logs"]:
            if path == public_prefix or path.startswith(public_prefix + "/") or path.startswith(public_prefix + "?"):
                return await call_next(request)

        # 检查登录
        user = get_current_user(request)
        if not user:
            return JSONResponse({"detail": "未登录"}, status_code=401)

        return await call_next(request)

    # API 路由
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(nodes_router)
    app.include_router(tasks_router)
    app.include_router(configs_router)
    app.include_router(images_router)
    app.include_router(reports_router)
    app.include_router(audit_router)
    app.include_router(purchased_router)
    app.include_router(logs_router)

    # Agent WebSocket 端点
    @app.websocket("/ws/agent")
    async def ws_agent(websocket: WebSocket):
        await agent_ws_endpoint(websocket)

    # Dashboard WebSocket 端点
    @app.websocket("/ws/dashboard")
    async def ws_dashboard(websocket: WebSocket):
        await websocket.accept()
        node_manager.add_dashboard(websocket)
        try:
            for node in node_manager.get_all():
                await websocket.send_text(make_message(MSG_STATUS_UPDATE, {
                    "node_id": node.node_id,
                    "name": node.name,
                    "status": "online" if node.online else "offline",
                    "task_status": node.task_status,
                    "task_progress": node.task_progress,
                }))
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            node_manager.remove_dashboard(websocket)

    # 静态文件 + 前端入口
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/")
        async def index():
            return FileResponse(os.path.join(static_dir, "index.html"))

    return app
