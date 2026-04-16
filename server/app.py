"""
FastAPI 应用工厂。
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from server.db.database import init_db
from server.ws.agent_handler import agent_ws_endpoint
from server.api.nodes import router as nodes_router
from server.api.tasks import router as tasks_router
from server.api.configs import router as configs_router
from server.api.images import router as images_router
from server.api.reports import router as reports_router
from server.services.node_manager import node_manager
from shared.protocol import parse_message, make_message, MSG_STATUS_UPDATE

logger = logging.getLogger("server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期。"""
    await init_db()
    logger.info("数据库已初始化")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="1688 采购管理系统", lifespan=lifespan)

    # API 路由
    app.include_router(nodes_router)
    app.include_router(tasks_router)
    app.include_router(configs_router)
    app.include_router(images_router)
    app.include_router(reports_router)

    # Agent WebSocket 端点
    @app.websocket("/ws/agent")
    async def ws_agent(websocket: WebSocket):
        await agent_ws_endpoint(websocket)

    # Dashboard WebSocket 端点（浏览器连接，接收实时推送）
    @app.websocket("/ws/dashboard")
    async def ws_dashboard(websocket: WebSocket):
        await websocket.accept()
        node_manager.add_dashboard(websocket)
        logger.info("Dashboard 客户端已连接")
        try:
            # 发送当前所有节点状态
            for node in node_manager.get_all():
                await websocket.send_text(make_message(MSG_STATUS_UPDATE, {
                    "node_id": node.node_id,
                    "name": node.name,
                    "status": "online" if node.online else "offline",
                    "task_status": node.task_status,
                    "task_progress": node.task_progress,
                }))
            # 保持连接
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            node_manager.remove_dashboard(websocket)
            logger.info("Dashboard 客户端已断开")

    # 静态文件（前端）
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/")
        async def index():
            return FileResponse(os.path.join(static_dir, "index.html"))

    return app
