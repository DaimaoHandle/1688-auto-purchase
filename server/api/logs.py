"""
日志查询 API。
"""
from fastapi import APIRouter
from typing import Optional

from server.db.database import get_db

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def query_logs(
    node_id: Optional[str] = None,
    task_id: Optional[str] = None,
    level: Optional[str] = None,
    event_type: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 200,
):
    """查询日志，支持多维度筛选。"""
    db = await get_db()
    try:
        conditions = []
        params = []

        if node_id:
            conditions.append("node_id = ?")
            params.append(node_id)
        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)
        if level:
            conditions.append("level = ?")
            params.append(level)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if search:
            conditions.append("message LIKE ?")
            params.append(f"%{search}%")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        cursor = await db.execute(
            f"SELECT * FROM task_logs {where} ORDER BY id DESC LIMIT ?",
            params
        )
        rows = await cursor.fetchall()
        # 反转为时间正序
        return [
            {
                "id": r["id"], "task_id": r["task_id"], "node_id": r["node_id"],
                "level": r["level"], "event_type": r["event_type"] if "event_type" in r.keys() else "runtime",
                "phase": r["phase"] if "phase" in r.keys() else "",
                "message": r["message"], "timestamp": r["timestamp"],
            }
            for r in reversed(list(rows))
        ]
    finally:
        await db.close()
