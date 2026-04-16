"""
Agent WebSocket 端点 — 处理采购服务器的连接、注册、心跳和消息转发。
"""
import logging
from fastapi import WebSocket, WebSocketDisconnect

from shared.protocol import (
    parse_message, make_message,
    MSG_REGISTER, MSG_REGISTER_ACK, MSG_HEARTBEAT, MSG_HEARTBEAT_ACK,
    MSG_STATUS_UPDATE, MSG_PROGRESS_UPDATE, MSG_LOG_ENTRY, MSG_TASK_REPORT, MSG_ERROR,
    STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED,
)
from server.services.node_manager import node_manager
from server.db.database import get_db

logger = logging.getLogger("server")


async def _verify_token(node_id: str, token: str) -> dict:
    """验证 Agent 的 node_id 和 token，返回节点信息或 None。"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name, token FROM nodes WHERE id = ?", (node_id,))
        row = await cursor.fetchone()
        if row and row["token"] == token:
            return {"id": row["id"], "name": row["name"]}
        return None
    finally:
        await db.close()


async def agent_ws_endpoint(websocket: WebSocket):
    """处理单个 Agent 的 WebSocket 连接。"""
    await websocket.accept()
    node_id = None

    try:
        # 等待第一条消息：必须是 register
        raw = await websocket.receive_text()
        msg = parse_message(raw)

        if msg["type"] != MSG_REGISTER:
            await websocket.send_text(make_message(MSG_REGISTER_ACK, {
                "ok": False, "error": "首条消息必须为 register"
            }))
            await websocket.close()
            return

        node_id = msg["payload"].get("node_id", "")
        token = msg["payload"].get("token", "")

        # 验证
        node_info = await _verify_token(node_id, token)
        if not node_info:
            await websocket.send_text(make_message(MSG_REGISTER_ACK, {
                "ok": False, "error": "node_id 或 token 无效"
            }))
            await websocket.close()
            return

        # 注册成功
        node_name = node_info["name"]
        node_manager.set_online(node_id, websocket, node_name)

        await websocket.send_text(make_message(MSG_REGISTER_ACK, {
            "ok": True, "node_name": node_name
        }))

        # 通知 Dashboard
        await node_manager.broadcast_to_dashboards(make_message(MSG_STATUS_UPDATE, {
            "node_id": node_id, "status": "online", "name": node_name
        }))

        logger.info(f"Agent 已注册: {node_id} ({node_name})")

        # 消息循环
        while True:
            raw = await websocket.receive_text()
            msg = parse_message(raw)
            msg_type = msg["type"]
            payload = msg["payload"]

            if msg_type == MSG_HEARTBEAT:
                node_manager.update_heartbeat(node_id)
                await websocket.send_text(make_message(MSG_HEARTBEAT_ACK, {}))

            elif msg_type == MSG_STATUS_UPDATE:
                task_id = payload.get("task_id", "")
                status = payload.get("status", "")
                node_manager.update_task_status(node_id, task_id, status)
                logger.info(f"[{node_id}] 状态: {status}")

                # 任务结束时清理
                if status in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
                    node_manager.clear_task(node_id)

                # 转发给 Dashboard
                payload["node_id"] = node_id
                await node_manager.broadcast_to_dashboards(make_message(MSG_STATUS_UPDATE, payload))

            elif msg_type == MSG_PROGRESS_UPDATE:
                node_manager.update_task_status(
                    node_id,
                    payload.get("task_id", ""),
                    "filling_cart",
                    progress=payload
                )
                payload["node_id"] = node_id
                await node_manager.broadcast_to_dashboards(make_message(MSG_PROGRESS_UPDATE, payload))

            elif msg_type == MSG_LOG_ENTRY:
                payload["node_id"] = node_id
                await node_manager.broadcast_to_dashboards(make_message(MSG_LOG_ENTRY, payload))

            elif msg_type == MSG_TASK_REPORT:
                payload["node_id"] = node_id
                logger.info(f"[{node_id}] 任务报告: 加购{payload.get('items_added', 0)}件, "
                           f"订单{payload.get('orders_created', 0)}笔")
                await node_manager.broadcast_to_dashboards(make_message(MSG_TASK_REPORT, payload))
                # 存储报告到数据库（后续阶段实现）

            elif msg_type == MSG_ERROR:
                payload["node_id"] = node_id
                logger.warning(f"[{node_id}] 错误: {payload.get('message', '')}")
                await node_manager.broadcast_to_dashboards(make_message(MSG_ERROR, payload))

    except WebSocketDisconnect:
        logger.info(f"Agent 断开: {node_id}")
    except Exception as e:
        logger.error(f"Agent WS 异常: {node_id} - {e}")
    finally:
        if node_id:
            node_manager.set_offline(node_id)
            await node_manager.broadcast_to_dashboards(make_message(MSG_STATUS_UPDATE, {
                "node_id": node_id, "status": "offline"
            }))
