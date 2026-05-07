"""
节点管理 API。
"""
import uuid
import secrets
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from server.db.database import get_db
from server.services.node_manager import node_manager
from shared.protocol import make_message, MSG_UPDATE_CODE
from server.api.audit import log_action

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


class CreateNodeRequest(BaseModel):
    name: str = ""
    remark: str = ""
    account_1688: str = ""
    buyer_name: str = ""
    buyer_id: str = ""
    card_no: str = ""
    alipay_account: str = ""
    buyer_phone: str = ""
    ship_address: str = ""


class UpdateNodeRequest(BaseModel):
    name: Optional[str] = None
    remark: Optional[str] = None
    account_1688: Optional[str] = None
    buyer_name: Optional[str] = None
    buyer_id: Optional[str] = None
    card_no: Optional[str] = None
    alipay_account: Optional[str] = None
    buyer_phone: Optional[str] = None
    ship_address: Optional[str] = None


@router.get("")
async def list_nodes():
    """列出所有节点（含在线状态和详细信息）。"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT id, name, remark, account_1688,
                      buyer_name, buyer_id, card_no, alipay_account, buyer_phone, ship_address,
                      created_at FROM nodes ORDER BY created_at"""
        )
        rows = await cursor.fetchall()

        result = []
        for row in rows:
            node_id = row["id"]
            state = node_manager.get(node_id)
            result.append({
                "id": node_id,
                "name": row["name"],
                "remark": row["remark"] or "",
                "account_1688": row["account_1688"] or "",
                "buyer_name": row["buyer_name"] or "",
                "buyer_id": row["buyer_id"] or "",
                "card_no": row["card_no"] or "",
                "alipay_account": row["alipay_account"] or "",
                "buyer_phone": row["buyer_phone"] or "",
                "ship_address": row["ship_address"] or "",
                "online": state.online if state else False,
                "remote_ip": state.remote_ip if state else "",
                "task_status": state.task_status if state else None,
                "task_progress": state.task_progress if state else None,
                "last_heartbeat": state.last_heartbeat.isoformat() if state and state.last_heartbeat else None,
                "created_at": row["created_at"],
            })
        return result
    finally:
        await db.close()


@router.get("/today-stats")
async def today_stats():
    """今日统计。"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT
                 COUNT(*) as total,
                 SUM(CASE WHEN errors_json = '[]' OR errors_json IS NULL THEN 1 ELSE 0 END) as success,
                 SUM(CASE WHEN errors_json != '[]' AND errors_json IS NOT NULL THEN 1 ELSE 0 END) as failed,
                 SUM(actual_amount) as amount
               FROM reports
               WHERE date(created_at) = date('now')"""
        )
        row = await cursor.fetchone()
        return {
            "today_success": row["success"] or 0,
            "today_failed": row["failed"] or 0,
            "today_amount": row["amount"] or 0,
        }
    finally:
        await db.close()


@router.post("/tokens")
async def create_node(req: CreateNodeRequest, request: Request = None):
    """生成新节点的 ID 和 Token。"""
    node_id = str(uuid.uuid4())[:8]
    token = secrets.token_hex(32)
    name = req.name or f"node-{node_id[:4]}"

    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO nodes (id, name, token, remark, account_1688,
               buyer_name, buyer_id, card_no, alipay_account, buyer_phone, ship_address)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (node_id, name, token, req.remark, req.account_1688,
             req.buyer_name, req.buyer_id, req.card_no, req.alipay_account,
             req.buyer_phone, req.ship_address)
        )
        await db.commit()
    finally:
        await db.close()

    await log_action(request, "添加节点", node_id, f"名称: {name}")
    return {"node_id": node_id, "token": token, "name": name}


@router.get("/{node_id}")
async def get_node(node_id: str):
    """获取单个节点详情。"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT id, name, remark, account_1688,
                      buyer_name, buyer_id, card_no, alipay_account, buyer_phone, ship_address,
                      created_at FROM nodes WHERE id = ?""",
            (node_id,)
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "节点不存在")
        state = node_manager.get(node_id)
        return {
            "id": row["id"],
            "name": row["name"],
            "remark": row["remark"] or "",
            "account_1688": row["account_1688"] or "",
            "buyer_name": row["buyer_name"] or "",
            "buyer_id": row["buyer_id"] or "",
            "card_no": row["card_no"] or "",
            "alipay_account": row["alipay_account"] or "",
            "buyer_phone": row["buyer_phone"] or "",
            "ship_address": row["ship_address"] or "",
            "online": state.online if state else False,
            "task_status": state.task_status if state else None,
            "last_heartbeat": state.last_heartbeat.isoformat() if state and state.last_heartbeat else None,
            "created_at": row["created_at"],
        }
    finally:
        await db.close()


@router.patch("/{node_id}")
async def update_node(node_id: str, req: UpdateNodeRequest, request: Request = None):
    """更新节点信息。"""
    db = await get_db()
    try:
        # 构建动态更新
        fields = []
        values = []
        for field in ["name", "remark", "account_1688", "buyer_name", "buyer_id",
                       "card_no", "alipay_account", "buyer_phone", "ship_address"]:
            val = getattr(req, field, None)
            if val is not None:
                fields.append(f"{field} = ?")
                values.append(val)

        if not fields:
            raise HTTPException(400, "无更新内容")

        fields.append("updated_at = datetime('now')")
        values.append(node_id)

        sql = f"UPDATE nodes SET {', '.join(fields)} WHERE id = ?"
        cursor = await db.execute(sql, values)
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, "节点不存在")
    finally:
        await db.close()

    # 同步更新内存状态
    state = node_manager.get(node_id)
    if state and req.name is not None:
        state.name = req.name

    await log_action(request, "编辑节点", node_id)
    return {"ok": True}


@router.post("/{node_id}/update")
async def update_node_code(node_id: str, request: Request = None):
    """向指定节点发送代码更新指令（git pull）。"""
    node = node_manager.get(node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    if not node.online or not node.ws:
        raise HTTPException(400, "节点离线")
    await node.ws.send_text(make_message(MSG_UPDATE_CODE, {}))
    await log_action(request, "更新代码", node_id)
    return {"ok": True}


@router.delete("/{node_id}")
async def delete_node(node_id: str, request: Request = None):
    """删除节点。"""
    db = await get_db()
    try:
        await db.execute("DELETE FROM node_configs WHERE node_id = ?", (node_id,))
        await db.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        await db.commit()
    finally:
        await db.close()
    await log_action(request, "删除节点", node_id)
    return {"ok": True}
