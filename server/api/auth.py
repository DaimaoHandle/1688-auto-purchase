"""
认证 API — 登录、登出、当前用户信息。
"""
import hashlib
import secrets
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from server.db.database import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 内存 session 存储：{token: user_dict}
_sessions = {}


def hash_password(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()


def get_current_user(request: Request) -> dict:
    """从请求中获取当前登录用户，未登录返回 None。"""
    token = request.cookies.get("session_token", "")
    return _sessions.get(token)


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
    finally:
        await db.close()

    if not user:
        raise HTTPException(401, "账号或密码错误")
    if user["status"] != "active":
        raise HTTPException(403, "账号已禁用")

    token = secrets.token_hex(32)
    _sessions[token] = {
        "id": user["id"],
        "phone": user["phone"],
        "name": user["name"],
        "avatar": user["avatar"],
        "role": user["role"],
    }
    response.set_cookie("session_token", token, httponly=True, max_age=86400 * 7)
    return {"ok": True, "user": _sessions[token]}


@router.post("/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("session_token", "")
    _sessions.pop(token, None)
    response.delete_cookie("session_token")
    return {"ok": True}


@router.get("/me")
async def get_me(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "未登录")
    return user
