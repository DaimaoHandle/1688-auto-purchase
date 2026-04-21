"""
报告汇总 API。
"""
import csv
import io
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from typing import Optional

from server.db.database import get_db

router = APIRouter(prefix="/api/reports", tags=["reports"])

REPORT_COLUMNS = [
    ("created_at", "采购日期"),
    ("shop_name", "店铺"),
    ("operator", "操作人"),
    ("buyer_info", "买手信息"),
    ("orders_created", "订单笔数"),
    ("actual_amount", "采购金额"),
    ("target_amount", "计划采购金额"),
    ("items_added", "加购商品数"),
]


@router.get("")
async def list_reports(node_id: Optional[str] = None, limit: int = 100):
    """列出报告。"""
    db = await get_db()
    try:
        query = """SELECT r.id, r.task_id, r.node_id, n.name as node_name,
                          r.shop_name, r.operator, r.buyer_info,
                          r.items_added, r.orders_created,
                          r.actual_amount, r.target_amount,
                          r.errors_json, r.created_at
                   FROM reports r LEFT JOIN nodes n ON r.node_id = n.id"""
        params = []
        if node_id:
            query += " WHERE r.node_id = ?"
            params.append(node_id)
        query += " ORDER BY r.created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"],
                "task_id": r["task_id"],
                "node_id": r["node_id"],
                "node_name": r["node_name"] or r["node_id"],
                "shop_name": r["shop_name"] or "",
                "operator": r["operator"] or "-",
                "buyer_info": r["buyer_info"] or "",
                "items_added": r["items_added"],
                "orders_created": r["orders_created"],
                "actual_amount": r["actual_amount"] or 0,
                "target_amount": r["target_amount"] or 0,
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
        cursor = await db.execute(
            """SELECT COUNT(*) as total_tasks,
                      SUM(items_added) as total_items,
                      SUM(orders_created) as total_orders,
                      SUM(actual_amount) as total_amount
               FROM reports"""
        )
        total = await cursor.fetchone()

        cursor2 = await db.execute(
            """SELECT r.node_id, n.name as node_name,
                      COUNT(*) as tasks,
                      SUM(r.items_added) as items,
                      SUM(r.orders_created) as orders,
                      SUM(r.actual_amount) as amount
               FROM reports r LEFT JOIN nodes n ON r.node_id = n.id
               GROUP BY r.node_id ORDER BY orders DESC"""
        )
        by_node = await cursor2.fetchall()

        return {
            "total_tasks": total["total_tasks"] or 0,
            "total_items": total["total_items"] or 0,
            "total_orders": total["total_orders"] or 0,
            "total_amount": total["total_amount"] or 0,
            "by_node": [
                {
                    "node_id": r["node_id"],
                    "node_name": r["node_name"] or r["node_id"],
                    "tasks": r["tasks"],
                    "items": r["items"] or 0,
                    "orders": r["orders"] or 0,
                    "amount": r["amount"] or 0,
                }
                for r in by_node
            ]
        }
    finally:
        await db.close()


@router.get("/daily")
async def daily_stats(days: int = 7):
    """最近 N 天每日采购统计。"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT date(created_at) as day,
                      COUNT(*) as tasks,
                      SUM(items_added) as items,
                      SUM(orders_created) as orders,
                      SUM(actual_amount) as amount
               FROM reports
               WHERE created_at >= date('now', ?)
               GROUP BY date(created_at)
               ORDER BY day""",
            (f"-{days} days",)
        )
        rows = await cursor.fetchall()
        return [
            {"day": r["day"], "tasks": r["tasks"], "items": r["items"] or 0,
             "orders": r["orders"] or 0, "amount": r["amount"] or 0}
            for r in rows
        ]
    finally:
        await db.close()


@router.get("/export")
async def export_reports(node_id: Optional[str] = None):
    """导出报告为 CSV（Excel 兼容）。"""
    db = await get_db()
    try:
        query = """SELECT r.shop_name, r.operator, r.buyer_info,
                          r.items_added, r.orders_created,
                          r.actual_amount, r.target_amount, r.created_at
                   FROM reports r"""
        params = []
        if node_id:
            query += " WHERE r.node_id = ?"
            params.append(node_id)
        query += " ORDER BY r.created_at DESC"

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
    finally:
        await db.close()

    # 生成 CSV（UTF-8 BOM，Excel 直接打开不乱码）
    output = io.StringIO()
    output.write('\ufeff')  # UTF-8 BOM
    writer = csv.writer(output)

    # 表头
    writer.writerow([col[1] for col in REPORT_COLUMNS])

    # 数据
    for r in rows:
        writer.writerow([
            r["created_at"] or "",
            r["shop_name"] or "",
            r["operator"] or "-",
            r["buyer_info"] or "",
            r["orders_created"] or 0,
            r["actual_amount"] or 0,
            r["target_amount"] or 0,
            r["items_added"] or 0,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=purchase_reports.csv"}
    )
