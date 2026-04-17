"""
SQLite 数据库初始化与连接管理。
"""
import os
import aiosqlite

_DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_DB_PATH = os.path.join(_DB_DIR, "server.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    token           TEXT NOT NULL,
    remark          TEXT NOT NULL DEFAULT '',
    account_1688    TEXT NOT NULL DEFAULT '',
    buyer_name      TEXT NOT NULL DEFAULT '',
    buyer_id        TEXT NOT NULL DEFAULT '',
    card_no         TEXT NOT NULL DEFAULT '',
    alipay_account  TEXT NOT NULL DEFAULT '',
    buyer_phone     TEXT NOT NULL DEFAULT '',
    ship_address    TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS node_configs (
    node_id     TEXT PRIMARY KEY REFERENCES nodes(id),
    config_json TEXT NOT NULL DEFAULT '{}',
    image_id    TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS images (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    data        BLOB NOT NULL,
    size_bytes  INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    node_id     TEXT NOT NULL REFERENCES nodes(id),
    status      TEXT NOT NULL DEFAULT 'pending',
    config_json TEXT NOT NULL DEFAULT '{}',
    started_at  TEXT,
    finished_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reports (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    node_id         TEXT NOT NULL,
    shop_name       TEXT NOT NULL DEFAULT '',
    operator        TEXT NOT NULL DEFAULT '',
    buyer_info      TEXT NOT NULL DEFAULT '',
    items_added     INTEGER NOT NULL DEFAULT 0,
    orders_created  INTEGER NOT NULL DEFAULT 0,
    actual_amount   REAL NOT NULL DEFAULT 0.0,
    target_amount   REAL NOT NULL DEFAULT 0.0,
    errors_json     TEXT DEFAULT '[]',
    report_json     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    phone       TEXT NOT NULL UNIQUE,
    password    TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    avatar      TEXT NOT NULL DEFAULT '/static/default_avatar.svg',
    role        TEXT NOT NULL DEFAULT 'user',
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT '',
    user_name   TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    node_id     TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    node_id     TEXT NOT NULL,
    level       TEXT NOT NULL DEFAULT 'INFO',
    message     TEXT NOT NULL,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def get_db() -> aiosqlite.Connection:
    """获取数据库连接。"""
    os.makedirs(_DB_DIR, exist_ok=True)
    db = await aiosqlite.connect(_DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def init_db():
    """初始化数据库表结构，创建默认管理员。"""
    import hashlib, uuid
    db = await get_db()
    try:
        await db.executescript(_SCHEMA)
        # 创建默认管理员（如果不存在）
        cursor = await db.execute("SELECT id FROM users WHERE phone = 'admin'")
        if not await cursor.fetchone():
            admin_id = str(uuid.uuid4())[:8]
            pwd_hash = hashlib.sha256("admin".encode()).hexdigest()
            await db.execute(
                "INSERT INTO users (id, phone, password, name, role) VALUES (?, ?, ?, ?, ?)",
                (admin_id, "admin", pwd_hash, "管理员", "admin")
            )
        await db.commit()
    finally:
        await db.close()
