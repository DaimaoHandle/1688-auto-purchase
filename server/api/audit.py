"""
操作日志审计 API。
记录谁在什么时间对哪个节点做了什么操作。
"""
import uuid
import json
from datetime import datetime
from fastapi import APIRouter, Request
from typing import Optional

from server.db.database import get_db
from server.api.auth import get_current_user

router = APIRouter(prefix="/api/audit", tags=["audit"])


async def log_action(request: Request, action: str, node_id: str = "", detail: str = ""):
    """记录一条操作日志。"""
    user = get_current_user(request) if request else None
    user_name = user["name"] if user else "系统"
    user_id = user["id"] if user else ""

    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO audit_logs (id, user_id, user_name, action, node_id, detail)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4())[:8], user_id, user_name, action, node_id, detail)
        )
        await db.commit()
    finally:
        await db.close()


@router.get("")
async def list_audit_logs(limit: int = 100, node_id: Optional[str] = None, request: Request = None):
    """查询操作日志。"""
    db = await get_db()
    try:
        if node_id:
            cursor = await db.execute(
                "SELECT * FROM audit_logs WHERE node_id = ? ORDER BY created_at DESC LIMIT ?",
                (node_id, limit)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"],
                "user_name": r["user_name"],
                "action": r["action"],
                "node_id": r["node_id"],
                "detail": r["detail"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    finally:
        await db.close()
