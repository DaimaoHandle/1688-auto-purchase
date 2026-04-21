"""
用户管理 API — 增删改查（仅管理员可操作）。
"""
import uuid
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from server.db.database import get_db
from server.api.auth import hash_password, get_current_user

router = APIRouter(prefix="/api/users", tags=["users"])


async def require_admin(request: Request):
    user = await get_current_user(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


class CreateUserRequest(BaseModel):
    phone: str
    password: str
    name: str = ""
    role: str = "user"


class UpdateUserRequest(BaseModel):
    phone: Optional[str] = None
    password: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None


@router.get("")
async def list_users(request: Request):
    await require_admin(request)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, phone, name, avatar, role, status, created_at FROM users ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        return [
            {"id": r["id"], "phone": r["phone"], "name": r["name"], "avatar": r["avatar"],
             "role": r["role"], "status": r["status"], "created_at": r["created_at"]}
            for r in rows
        ]
    finally:
        await db.close()


@router.post("")
async def create_user(req: CreateUserRequest, request: Request):
    await require_admin(request)
    if len(req.password) < 8:
        raise HTTPException(400, "密码至少8位")
    if not req.phone:
        raise HTTPException(400, "手机号不能为空")

    user_id = str(uuid.uuid4())[:8]
    db = await get_db()
    try:
        # 检查手机号唯一
        cursor = await db.execute("SELECT id FROM users WHERE phone = ?", (req.phone,))
        if await cursor.fetchone():
            raise HTTPException(400, "该手机号已注册")

        await db.execute(
            "INSERT INTO users (id, phone, password, name, role) VALUES (?, ?, ?, ?, ?)",
            (user_id, req.phone, hash_password(req.password), req.name or req.phone, req.role)
        )
        await db.commit()
    finally:
        await db.close()

    return {"id": user_id, "phone": req.phone, "name": req.name}


@router.patch("/{user_id}")
async def update_user(user_id: str, req: UpdateUserRequest, request: Request):
    await require_admin(request)
    db = await get_db()
    try:
        fields = []
        values = []
        if req.phone is not None:
            fields.append("phone = ?")
            values.append(req.phone)
        if req.password is not None:
            if len(req.password) < 8:
                raise HTTPException(400, "密码至少8位")
            fields.append("password = ?")
            values.append(hash_password(req.password))
        if req.name is not None:
            fields.append("name = ?")
            values.append(req.name)
        if req.role is not None:
            fields.append("role = ?")
            values.append(req.role)
        if req.status is not None:
            fields.append("status = ?")
            values.append(req.status)

        if not fields:
            raise HTTPException(400, "无更新内容")

        fields.append("updated_at = datetime('now')")
        values.append(user_id)
        await db.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
        await db.commit()
    finally:
        await db.close()

    return {"ok": True}


@router.delete("/{user_id}")
async def delete_user(user_id: str, request: Request):
    cur_user = await require_admin(request)
    if cur_user["id"] == user_id:
        raise HTTPException(400, "不能删除自己")
    db = await get_db()
    try:
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()
    return {"ok": True}
