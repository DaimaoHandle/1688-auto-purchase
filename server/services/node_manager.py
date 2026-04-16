"""
节点状态管理 — 维护所有 Agent 节点的在线状态和 WebSocket 连接。
"""
import logging
from datetime import datetime
from typing import Optional
from fastapi import WebSocket

logger = logging.getLogger("server")


class NodeState:
    """单个节点的运行时状态。"""

    def __init__(self, node_id: str, name: str = ""):
        self.node_id = node_id
        self.name = name
        self.online = False
        self.ws: Optional[WebSocket] = None
        self.remote_ip: str = ""
        self.last_heartbeat: Optional[datetime] = None
        self.current_task_id: Optional[str] = None
        self.task_status: Optional[str] = None
        self.task_progress: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "online": self.online,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "current_task_id": self.current_task_id,
            "task_status": self.task_status,
            "task_progress": self.task_progress,
        }


class NodeManager:
    """管理所有节点的运行时状态。"""

    def __init__(self):
        self._nodes: dict[str, NodeState] = {}
        # 连接到 /ws/dashboard 的浏览器客户端，用于实时推送
        self._dashboard_clients: list[WebSocket] = []

    def get_or_create(self, node_id: str, name: str = "") -> NodeState:
        if node_id not in self._nodes:
            self._nodes[node_id] = NodeState(node_id, name)
        return self._nodes[node_id]

    def get(self, node_id: str) -> Optional[NodeState]:
        return self._nodes.get(node_id)

    def get_all(self) -> list[NodeState]:
        return list(self._nodes.values())

    def set_online(self, node_id: str, ws: WebSocket, name: str = "", remote_ip: str = ""):
        node = self.get_or_create(node_id, name)
        node.online = True
        node.ws = ws
        node.remote_ip = remote_ip
        node.last_heartbeat = datetime.now()
        if name:
            node.name = name
        logger.info(f"节点上线: {node_id} ({name}) IP={remote_ip}")

    def set_offline(self, node_id: str):
        node = self.get(node_id)
        if node:
            node.online = False
            node.ws = None
            logger.info(f"节点离线: {node_id} ({node.name})")

    def update_heartbeat(self, node_id: str):
        node = self.get(node_id)
        if node:
            node.last_heartbeat = datetime.now()

    def update_task_status(self, node_id: str, task_id: str, status: str, progress: dict = None):
        node = self.get(node_id)
        if node:
            node.current_task_id = task_id
            node.task_status = status
            if progress:
                node.task_progress = progress

    def clear_task(self, node_id: str):
        node = self.get(node_id)
        if node:
            node.current_task_id = None
            node.task_status = None
            node.task_progress = None

    # Dashboard 客户端管理
    def add_dashboard(self, ws: WebSocket):
        self._dashboard_clients.append(ws)

    def remove_dashboard(self, ws: WebSocket):
        if ws in self._dashboard_clients:
            self._dashboard_clients.remove(ws)

    async def broadcast_to_dashboards(self, message: str):
        """向所有连接的 Dashboard 浏览器推送消息。"""
        disconnected = []
        for ws in self._dashboard_clients:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.remove_dashboard(ws)


# 全局单例
node_manager = NodeManager()
