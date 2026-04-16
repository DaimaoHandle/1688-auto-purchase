"""
节点配置管理 API。
"""
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from server.db.database import get_db

router = APIRouter(prefix="/api/nodes", tags=["configs"])

# 默认配置模板
DEFAULT_CONFIG = {
    "browser": {
        "type": "chromium",
        "headless": False,
        "slow_mo": 100,
        "profile_dir": "~/1688/browser_profile"
    },
    "search": {
        "image_path": "",
        "target_shop_name": ""
    },
    "cart": {
        "target_amount": 2000,
        "amount_strategy": "not_exceed",
        "max_items": 200,
        "order_limit": 500,
        "shipping_reserve": 15
    },
    "timeouts": {
        "page_load": 30000,
        "element_wait": 10000,
        "login_wait": 120000
    },
    "logging": {
        "level": "INFO",
        "file": "logs/app.log"
    }
}


class SaveConfigRequest(BaseModel):
    config: dict
    image_id: Optional[str] = None


@router.get("/{node_id}/config")
async def get_config(node_id: str):
    """获取节点的采购配置。"""
    db = await get_db()
    try:
        # 确认节点存在
        cursor = await db.execute("SELECT id FROM nodes WHERE id = ?", (node_id,))
        if not await cursor.fetchone():
            raise HTTPException(404, "节点不存在")

        cursor = await db.execute(
            "SELECT config_json, image_id FROM node_configs WHERE node_id = ?", (node_id,)
        )
        row = await cursor.fetchone()
        if row:
            return {
                "config": json.loads(row["config_json"]),
                "image_id": row["image_id"],
            }
        else:
            return {"config": DEFAULT_CONFIG, "image_id": None}
    finally:
        await db.close()


@router.put("/{node_id}/config")
async def save_config(node_id: str, req: SaveConfigRequest):
    """保存/更新节点的采购配置。"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM nodes WHERE id = ?", (node_id,))
        if not await cursor.fetchone():
            raise HTTPException(404, "节点不存在")

        config_json = json.dumps(req.config, ensure_ascii=False)
        await db.execute(
            """INSERT INTO node_configs (node_id, config_json, image_id, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(node_id) DO UPDATE SET
                 config_json = excluded.config_json,
                 image_id = excluded.image_id,
                 updated_at = datetime('now')""",
            (node_id, config_json, req.image_id)
        )
        await db.commit()
    finally:
        await db.close()

    return {"ok": True}
