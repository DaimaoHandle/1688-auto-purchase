"""
任务计划 API — 批量配置和调度多节点采购任务。
"""
import uuid
import json
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List

from server.db.database import get_db
from server.services.node_manager import node_manager
from server.api.auth import get_current_user
from shared.protocol import make_message, MSG_START_TASK

router = APIRouter(prefix="/api/task-plans", tags=["task-plans"])


class PlanItem(BaseModel):
    node_id: str
    shop_name: str = ""
    image_id: Optional[str] = None
    target_amount: float = 2000
    purchase_mode: str = "normal"
    order_limit: float = 500
    shipping_reserve: float = 15
    max_items: int = 200


class CreatePlanRequest(BaseModel):
    name: str = ""
    scheduled_at: Optional[str] = None  # ISO 时间，空则立即执行
    items: List[PlanItem]


@router.post("")
async def create_plan(req: CreatePlanRequest, request: Request):
    """创建任务计划。"""
    user = get_current_user(request)
    if not req.items:
        raise HTTPException(400, "至少选择一个节点")

    plan_id = str(uuid.uuid4())[:8]
    plan_name = req.name or f"采购计划 {datetime.now().strftime('%m-%d %H:%M')}"

    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO task_plans (id, name, scheduled_at, status, created_by) VALUES (?, ?, ?, ?, ?)",
            (plan_id, plan_name, req.scheduled_at or None, "pending", user["name"] if user else "")
        )
        for item in req.items:
            await db.execute(
                """INSERT INTO task_plan_items
                   (plan_id, node_id, shop_name, image_id, target_amount, purchase_mode, order_limit, shipping_reserve, max_items)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (plan_id, item.node_id, item.shop_name, item.image_id,
                 item.target_amount, item.purchase_mode, item.order_limit,
                 item.shipping_reserve, item.max_items)
            )
        await db.commit()
    finally:
        await db.close()

    # 如果没有定时，立即执行
    if not req.scheduled_at:
        await _execute_plan(plan_id)

    return {"plan_id": plan_id, "name": plan_name}


@router.get("")
async def list_plans(limit: int = 50):
    """列出任务计划。"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM task_plans ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        plans = []
        for row in await cursor.fetchall():
            # 获取计划项
            items_cursor = await db.execute(
                """SELECT pi.*, n.name as node_name FROM task_plan_items pi
                   LEFT JOIN nodes n ON pi.node_id = n.id
                   WHERE pi.plan_id = ?""",
                (row["id"],)
            )
            items = await items_cursor.fetchall()
            plans.append({
                "id": row["id"],
                "name": row["name"],
                "scheduled_at": row["scheduled_at"],
                "status": row["status"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "items": [
                    {
                        "node_id": it["node_id"],
                        "node_name": it["node_name"] or it["node_id"],
                        "shop_name": it["shop_name"],
                        "image_id": it["image_id"],
                        "target_amount": it["target_amount"],
                        "purchase_mode": it["purchase_mode"],
                        "status": it["status"],
                        "task_id": it["task_id"],
                    }
                    for it in items
                ],
                "node_count": len(items),
            })
        return plans
    finally:
        await db.close()


@router.post("/{plan_id}/execute")
async def execute_plan(plan_id: str):
    """手动执行一个计划。"""
    await _execute_plan(plan_id)
    return {"ok": True}


@router.delete("/{plan_id}")
async def delete_plan(plan_id: str):
    """删除计划。"""
    db = await get_db()
    try:
        await db.execute("DELETE FROM task_plan_items WHERE plan_id = ?", (plan_id,))
        await db.execute("DELETE FROM task_plans WHERE id = ?", (plan_id,))
        await db.commit()
    finally:
        await db.close()
    return {"ok": True}


async def _execute_plan(plan_id: str):
    """执行计划：向每个节点发送启动任务指令。"""
    db = await get_db()
    try:
        # 更新计划状态
        await db.execute("UPDATE task_plans SET status = 'running' WHERE id = ?", (plan_id,))

        # 获取计划项
        cursor = await db.execute("SELECT * FROM task_plan_items WHERE plan_id = ?", (plan_id,))
        items = await cursor.fetchall()

        for item in items:
            node = node_manager.get(item["node_id"])
            if not node or not node.online or not node.ws:
                await db.execute(
                    "UPDATE task_plan_items SET status = 'skipped' WHERE plan_id = ? AND node_id = ?",
                    (plan_id, item["node_id"])
                )
                continue

            # 构建配置
            config = {
                "browser": {"type": "chromium", "headless": False, "slow_mo": 100, "profile_dir": "~/1688/browser_profile"},
                "search": {"image_path": "", "target_shop_name": item["shop_name"]},
                "cart": {
                    "target_amount": item["target_amount"],
                    "amount_strategy": "not_exceed",
                    "purchase_mode": item["purchase_mode"],
                    "max_items": item["max_items"],
                    "order_limit": item["order_limit"],
                    "shipping_reserve": item["shipping_reserve"],
                },
                "timeouts": {"page_load": 30000, "element_wait": 10000, "login_wait": 120000},
                "logging": {"level": "INFO", "file": "logs/app.log"},
            }

            # 图片 URL
            image_url = ""
            image_filename = ""
            if item["image_id"]:
                img_cursor = await db.execute("SELECT filename FROM images WHERE id = ?", (item["image_id"],))
                img_row = await img_cursor.fetchone()
                if img_row:
                    image_filename = img_row["filename"]
                    # 构建下载 URL（Agent 通过 HTTP GET 下载）
                    image_url = f"http://1688.mfjx.cloud/api/images/{item['image_id']}"

            task_id = str(uuid.uuid4())[:8]

            await node.ws.send_text(make_message(MSG_START_TASK, {
                "task_id": task_id,
                "config": config,
                "image_url": image_url,
                "image_filename": image_filename,
            }))

            await db.execute(
                "UPDATE task_plan_items SET status = 'running', task_id = ? WHERE plan_id = ? AND node_id = ?",
                (task_id, plan_id, item["node_id"])
            )
            node_manager.update_task_status(item["node_id"], task_id, "starting")

        await db.commit()
    finally:
        await db.close()
