"""
采购历史 API — 所有采购端共享的去重记录。
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from server.db.database import get_db

router = APIRouter(prefix="/api/purchased", tags=["purchased"])


class MarkPurchasedRequest(BaseModel):
    shop_name: str
    offer_ids: list
    node_id: str = ""


class CheckPurchasedRequest(BaseModel):
    shop_name: str
    offer_ids: list


@router.post("/check")
async def check_purchased(req: CheckPurchasedRequest):
    """检查哪些 offerId 已采购过。返回已采购的 offerId 列表。"""
    if not req.offer_ids:
        return {"purchased": []}

    db = await get_db()
    try:
        placeholders = ",".join(["?"] * len(req.offer_ids))
        cursor = await db.execute(
            f"SELECT offer_id FROM purchased_items WHERE shop_name = ? AND offer_id IN ({placeholders})",
            [req.shop_name] + req.offer_ids
        )
        rows = await cursor.fetchall()
        return {"purchased": [r["offer_id"] for r in rows]}
    finally:
        await db.close()


@router.post("/mark")
async def mark_purchased(req: MarkPurchasedRequest):
    """批量标记 offerId 为已采购。"""
    if not req.offer_ids:
        return {"ok": True, "added": 0}

    db = await get_db()
    try:
        added = 0
        for oid in req.offer_ids:
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO purchased_items (shop_name, offer_id, node_id) VALUES (?, ?, ?)",
                    (req.shop_name, oid, req.node_id)
                )
                added += 1
            except Exception:
                pass
        await db.commit()
        return {"ok": True, "added": added}
    finally:
        await db.close()


@router.get("")
async def list_purchased(shop_name: Optional[str] = None, limit: int = 200):
    """查询采购历史。"""
    db = await get_db()
    try:
        if shop_name:
            cursor = await db.execute(
                "SELECT shop_name, offer_id, node_id, created_at FROM purchased_items WHERE shop_name = ? ORDER BY created_at DESC LIMIT ?",
                (shop_name, limit)
            )
        else:
            cursor = await db.execute(
                "SELECT shop_name, offer_id, node_id, created_at FROM purchased_items ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
        rows = await cursor.fetchall()
        return [{"shop_name": r["shop_name"], "offer_id": r["offer_id"], "node_id": r["node_id"], "created_at": r["created_at"]} for r in rows]
    finally:
        await db.close()


@router.get("/count")
async def purchased_count(shop_name: Optional[str] = None):
    """统计已采购商品数。"""
    db = await get_db()
    try:
        if shop_name:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM purchased_items WHERE shop_name = ?", (shop_name,))
        else:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM purchased_items")
        row = await cursor.fetchone()
        return {"count": row["cnt"]}
    finally:
        await db.close()
