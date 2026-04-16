"""
节点管理 API。
"""
import uuid
import secrets
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.db.database import get_db
from server.services.node_manager import node_manager

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


class CreateNodeRequest(BaseModel):
    name: str = ""


class UpdateNodeRequest(BaseModel):
    name: str


@router.get("")
async def list_nodes():
    """列出所有节点（含在线状态）。"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name, created_at FROM nodes ORDER BY created_at")
        rows = await cursor.fetchall()

        result = []
        for row in rows:
            node_id = row["id"]
            state = node_manager.get(node_id)
            result.append({
                "id": node_id,
                "name": row["name"],
                "online": state.online if state else False,
                "task_status": state.task_status if state else None,
                "task_progress": state.task_progress if state else None,
                "last_heartbeat": state.last_heartbeat.isoformat() if state and state.last_heartbeat else None,
                "created_at": row["created_at"],
            })
        return result
    finally:
        await db.close()


@router.post("/tokens")
async def create_node(req: CreateNodeRequest):
    """生成新节点的 ID 和 Token。"""
    node_id = str(uuid.uuid4())[:8]
    token = secrets.token_hex(32)
    name = req.name or f"node-{node_id[:4]}"

    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO nodes (id, name, token) VALUES (?, ?, ?)",
            (node_id, name, token)
        )
        await db.commit()
    finally:
        await db.close()

    return {"node_id": node_id, "token": token, "name": name}


@router.patch("/{node_id}")
async def update_node(node_id: str, req: UpdateNodeRequest):
    """更新节点名称。"""
    db = await get_db()
    try:
        cursor = await db.execute("UPDATE nodes SET name = ?, updated_at = datetime('now') WHERE id = ?",
                                  (req.name, node_id))
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, "节点不存在")
    finally:
        await db.close()

    # 同步更新内存状态
    state = node_manager.get(node_id)
    if state:
        state.name = req.name

    return {"ok": True}


@router.delete("/{node_id}")
async def delete_node(node_id: str):
    """删除节点。"""
    db = await get_db()
    try:
        await db.execute("DELETE FROM node_configs WHERE node_id = ?", (node_id,))
        await db.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        await db.commit()
    finally:
        await db.close()
    return {"ok": True}
