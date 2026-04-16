"""
搜索图片管理 API。
"""
import uuid
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import Response

from server.db.database import get_db

router = APIRouter(prefix="/api/images", tags=["images"])

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB


@router.post("")
async def upload_image(file: UploadFile = File(...)):
    """上传搜索图片。"""
    data = await file.read()
    if len(data) > MAX_IMAGE_SIZE:
        raise HTTPException(400, f"图片大小超过限制（最大 {MAX_IMAGE_SIZE // 1024 // 1024}MB）")

    image_id = str(uuid.uuid4())[:8]
    filename = file.filename or "image.png"

    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO images (id, filename, data, size_bytes) VALUES (?, ?, ?, ?)",
            (image_id, filename, data, len(data))
        )
        await db.commit()
    finally:
        await db.close()

    return {"id": image_id, "filename": filename, "size": len(data)}


@router.get("")
async def list_images():
    """列出所有已上传的图片。"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, filename, size_bytes, uploaded_at FROM images ORDER BY uploaded_at DESC"
        )
        rows = await cursor.fetchall()
        return [
            {"id": r["id"], "filename": r["filename"], "size": r["size_bytes"], "uploaded_at": r["uploaded_at"]}
            for r in rows
        ]
    finally:
        await db.close()


@router.get("/{image_id}")
async def get_image(image_id: str):
    """下载图片（Agent 用此接口获取搜索图片）。"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT data, filename FROM images WHERE id = ?", (image_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "图片不存在")

        # 根据文件名推断 content type
        filename = row["filename"].lower()
        if filename.endswith(".png"):
            media_type = "image/png"
        elif filename.endswith(".jpg") or filename.endswith(".jpeg"):
            media_type = "image/jpeg"
        else:
            media_type = "application/octet-stream"

        return Response(content=row["data"], media_type=media_type)
    finally:
        await db.close()


@router.delete("/{image_id}")
async def delete_image(image_id: str):
    """删除图片。"""
    db = await get_db()
    try:
        await db.execute("DELETE FROM images WHERE id = ?", (image_id,))
        await db.commit()
    finally:
        await db.close()
    return {"ok": True}
