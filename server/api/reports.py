"""
报告汇总 API。
"""
import json
from fastapi import APIRouter
from typing import Optional

from server.db.database import get_db

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("")
async def list_reports(node_id: Optional[str] = None, limit: int = 50):
    """列出报告，支持按节点筛选。"""
    db = await get_db()
    try:
        if node_id:
            cursor = await db.execute(
                """SELECT r.id, r.task_id, r.node_id, n.name as node_name,
                          r.items_added, r.orders_created, r.errors_json, r.created_at
                   FROM reports r LEFT JOIN nodes n ON r.node_id = n.id
                   WHERE r.node_id = ?
                   ORDER BY r.created_at DESC LIMIT ?""",
                (node_id, limit)
            )
        else:
            cursor = await db.execute(
                """SELECT r.id, r.task_id, r.node_id, n.name as node_name,
                          r.items_added, r.orders_created, r.errors_json, r.created_at
                   FROM reports r LEFT JOIN nodes n ON r.node_id = n.id
                   ORDER BY r.created_at DESC LIMIT ?""",
                (limit,)
            )
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"],
                "task_id": r["task_id"],
                "node_id": r["node_id"],
                "node_name": r["node_name"] or r["node_id"],
                "items_added": r["items_added"],
                "orders_created": r["orders_created"],
                "errors": json.loads(r["errors_json"]) if r["errors_json"] else [],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    finally:
        await db.close()


@router.get("/summary")
async def report_summary():
    """汇总统计。"""
    db = await get_db()
    try:
        # 总计
        cursor = await db.execute(
            """SELECT COUNT(*) as total_tasks,
                      SUM(items_added) as total_items,
                      SUM(orders_created) as total_orders
               FROM reports"""
        )
        total = await cursor.fetchone()

        # 按节点统计
        cursor2 = await db.execute(
            """SELECT r.node_id, n.name as node_name,
                      COUNT(*) as tasks,
                      SUM(r.items_added) as items,
                      SUM(r.orders_created) as orders
               FROM reports r LEFT JOIN nodes n ON r.node_id = n.id
               GROUP BY r.node_id
               ORDER BY orders DESC"""
        )
        by_node = await cursor2.fetchall()

        return {
            "total_tasks": total["total_tasks"] or 0,
            "total_items": total["total_items"] or 0,
            "total_orders": total["total_orders"] or 0,
            "by_node": [
                {
                    "node_id": r["node_id"],
                    "node_name": r["node_name"] or r["node_id"],
                    "tasks": r["tasks"],
                    "items": r["items"] or 0,
                    "orders": r["orders"] or 0,
                }
                for r in by_node
            ]
        }
    finally:
        await db.close()
