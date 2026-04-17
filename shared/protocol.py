"""
Agent <-> Server 通信协议定义。
所有 WebSocket 消息均为 JSON 格式，包含 type 和 payload 字段。
"""
import json
import uuid
from datetime import datetime, timezone


# ─── 消息类型常量 ──────────────────────────────────────────────────────────────

# 连接生命周期
MSG_REGISTER = "register"
MSG_REGISTER_ACK = "register_ack"
MSG_HEARTBEAT = "heartbeat"
MSG_HEARTBEAT_ACK = "heartbeat_ack"

# 任务控制（Server → Agent）
MSG_START_TASK = "start_task"
MSG_STOP_TASK = "stop_task"
MSG_APPROVE_CHECKOUT = "approve_checkout"
MSG_REJECT_CHECKOUT = "reject_checkout"

# 管理指令（Server → Agent）
MSG_UPDATE_CODE = "update_code"

# 状态上报（Agent → Server）
MSG_STATUS_UPDATE = "status_update"
MSG_PROGRESS_UPDATE = "progress_update"
MSG_CHECKOUT_PROGRESS = "checkout_progress"
MSG_LOG_ENTRY = "log_entry"
MSG_TASK_REPORT = "task_report"
MSG_ERROR = "error"

# 任务状态
STATUS_IDLE = "idle"
STATUS_STARTING = "starting"
STATUS_WAITING_LOGIN = "waiting_login"
STATUS_LOGGED_IN = "logged_in"
STATUS_SEARCHING = "searching"
STATUS_ENTERING_SHOP = "entering_shop"
STATUS_FILLING_CART = "filling_cart"
STATUS_CART_FILLED = "cart_filled"
STATUS_AWAITING_APPROVAL = "awaiting_approval"
STATUS_CHECKING_OUT = "checking_out"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

# 状态中文显示
STATUS_LABELS = {
    STATUS_IDLE: "空闲",
    STATUS_STARTING: "启动中",
    STATUS_WAITING_LOGIN: "等待登录",
    STATUS_LOGGED_IN: "已登录",
    STATUS_SEARCHING: "搜图中",
    STATUS_ENTERING_SHOP: "进入店铺",
    STATUS_FILLING_CART: "加购中",
    STATUS_CART_FILLED: "加购完成",
    STATUS_AWAITING_APPROVAL: "等待确认结算",
    STATUS_CHECKING_OUT: "结算中",
    STATUS_COMPLETED: "已完成",
    STATUS_FAILED: "异常",
    STATUS_CANCELLED: "已取消",
}


def make_message(msg_type: str, payload: dict = None) -> str:
    """构建 WebSocket 消息 JSON 字符串。"""
    msg = {
        "type": msg_type,
        "payload": payload or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "msg_id": str(uuid.uuid4())[:8],
    }
    return json.dumps(msg, ensure_ascii=False)


def parse_message(raw: str) -> dict:
    """解析 WebSocket 消息，返回 {type, payload, timestamp, msg_id}。"""
    try:
        msg = json.loads(raw)
        return {
            "type": msg.get("type", ""),
            "payload": msg.get("payload", {}),
            "timestamp": msg.get("timestamp", ""),
            "msg_id": msg.get("msg_id", ""),
        }
    except (json.JSONDecodeError, TypeError):
        return {"type": "unknown", "payload": {}, "timestamp": "", "msg_id": ""}
