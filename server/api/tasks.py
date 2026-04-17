"""
任务控制 API — 启动、停止、审批结算。
"""
import json
import uuid
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from server.db.database import get_db
from server.services.node_manager import node_manager
from shared.protocol import (
    make_message, MSG_START_TASK, MSG_STOP_TASK, MSG_APPROVE_CHECKOUT, MSG_REJECT_CHECKOUT, MSG_UPDATE_CODE,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("/nodes/{node_id}/start")
async def start_task(node_id: str, request: Request):
    """向指定节点发送启动任务指令。使用该节点在数据库中存储的配置和图片。"""
    node = node_manager.get(node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    if not node.online or not node.ws:
        raise HTTPException(400, "节点离线")
    if node.task_status and node.task_status not in ("idle", "completed", "failed", "cancelled"):
        raise HTTPException(400, f"节点正在执行任务: {node.task_status}")

    # 从数据库读取节点配置
    config = {}
    image_url = ""
    image_filename = ""

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT config_json, image_id FROM node_configs WHERE node_id = ?", (node_id,)
        )
        row = await cursor.fetchone()
        if row:
            config = json.loads(row["config_json"])
            image_id = row["image_id"]
            if image_id:
                # 构建图片下载 URL（Agent 通过 HTTP GET 下载）
                base_url = str(request.base_url).rstrip("/")
                image_url = f"{base_url}/api/images/{image_id}"
                # 获取文件名
                img_cursor = await db.execute("SELECT filename FROM images WHERE id = ?", (image_id,))
                img_row = await img_cursor.fetchone()
                if img_row:
                    image_filename = img_row["filename"]
    finally:
        await db.close()

    task_id = str(uuid.uuid4())[:8]

    await node.ws.send_text(make_message(MSG_START_TASK, {
        "task_id": task_id,
        "config": config,
        "image_url": image_url,
        "image_filename": image_filename,
    }))

    node_manager.update_task_status(node_id, task_id, "starting")

    return {"task_id": task_id}


@router.post("/nodes/{node_id}/stop")
async def stop_task(node_id: str):
    """向指定节点发送停止任务指令。"""
    node = node_manager.get(node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    if not node.online or not node.ws:
        raise HTTPException(400, "节点离线")

    await node.ws.send_text(make_message(MSG_STOP_TASK, {
        "task_id": node.current_task_id or "",
    }))

    return {"ok": True}


@router.post("/{task_id}/approve")
async def approve_checkout(task_id: str):
    """批准结算。"""
    # 找到拥有此任务的节点
    for node in node_manager.get_all():
        if node.current_task_id == task_id and node.online and node.ws:
            await node.ws.send_text(make_message(MSG_APPROVE_CHECKOUT, {
                "task_id": task_id,
            }))
            return {"ok": True}
    raise HTTPException(404, "未找到对应节点或节点离线")


@router.post("/{task_id}/reject")
async def reject_checkout(task_id: str):
    """拒绝结算。"""
    for node in node_manager.get_all():
        if node.current_task_id == task_id and node.online and node.ws:
            await node.ws.send_text(make_message(MSG_REJECT_CHECKOUT, {
                "task_id": task_id,
            }))
            return {"ok": True}
    raise HTTPException(404, "未找到对应节点或节点离线")


@router.post("/nodes/{node_id}/update")
async def update_node_code(node_id: str):
    """向指定节点发送代码更新指令（git pull）。"""
    node = node_manager.get(node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    if not node.online or not node.ws:
        raise HTTPException(400, "节点离线")

    await node.ws.send_text(make_message(MSG_UPDATE_CODE, {}))
    return {"ok": True}
