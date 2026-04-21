"""
认证 API — 登录、登出、当前用户信息。
Session 存储在数据库中，重启不丢失。
"""
import hashlib
import secrets
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from server.db.database import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


def hash_password(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()


async def get_current_user(request: Request) -> dict:
    """从请求中获取当前登录用户，未登录返回 None。"""
    token = request.cookies.get("session_token", "")
    if not token:
        return None
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT s.user_id, u.phone, u.name, u.avatar, u.role
               FROM sessions s JOIN users u ON s.user_id = u.id
               WHERE s.token = ? AND u.status = 'active'""",
            (token,)
        )
        row = await cursor.fetchone()
        if row:
            return {"id": row["user_id"], "phone": row["phone"], "name": row["name"],
                    "avatar": row["avatar"], "role": row["role"]}
        return None
    finally:
        await db.close()


class LoginRequest(BaseModel):
    phone: str
    password: str


@router.post("/login")
async def login(req: LoginRequest, response: Response):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, phone, name, avatar, role, status FROM users WHERE phone = ? AND password = ?",
            (req.phone, hash_password(req.password))
        )
        user = await cursor.fetchone()
        if not user:
            raise HTTPException(401, "账号或密码错误")
        if user["status"] != "active":
            raise HTTPException(403, "账号已禁用")

        token = secrets.token_hex(32)
        await db.execute(
            "INSERT INTO sessions (token, user_id) VALUES (?, ?)",
            (token, user["id"])
        )
        await db.commit()
    finally:
        await db.close()

    user_data = {"id": user["id"], "phone": user["phone"], "name": user["name"],
                 "avatar": user["avatar"], "role": user["role"]}
    response.set_cookie("session_token", token, httponly=True, max_age=86400 * 7)
    return {"ok": True, "user": user_data}


@router.post("/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("session_token", "")
    if token:
        db = await get_db()
        try:
            await db.execute("DELETE FROM sessions WHERE token = ?", (token,))
            await db.commit()
        finally:
            await db.close()
    response.delete_cookie("session_token")
    return {"ok": True}


@router.get("/me")
async def get_me(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "未登录")
    return user
