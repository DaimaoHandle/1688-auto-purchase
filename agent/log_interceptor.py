"""
日志拦截器 — 捕获 1688-auto logger 的输出，转发到 WebSocket。
增加结构化字段：phase（阶段）、event_type（事件/运行/原始）。
"""
import logging
import re
import asyncio
from datetime import datetime
from shared.protocol import make_message, MSG_LOG_ENTRY

# 关键事件关键词 → 归类为"事件"级别日志
_EVENT_KEYWORDS = [
    "浏览器已启动", "登录成功", "开始搜图", "找到目标店铺", "进入新品专区",
    "已进入新品专区", "今日无上新", "开始填充采购车", "已加入采购车",
    "加购完成", "等待确认结算", "开始结算", "订单提交成功", "结算完成",
    "任务完成", "采购车填充完成", "已发送消息", "翻页成功",
    "已点击全选", "已点击销量排序", "去重过滤",
    "收到取消指令", "用户中断", "校准",
]

# 状态关键词映射到阶段名
_PHASE_KEYWORDS = {
    "浏览器": "启动", "登录": "登录", "搜图": "搜图", "图搜": "搜图",
    "店铺": "找店", "新品": "新品", "加购": "加购", "填充采购车": "加购",
    "结算": "结算", "订单": "结算", "采购车": "采购车",
    "校准": "校准", "翻页": "翻页", "客服": "客服",
}


def _classify_log(message: str) -> tuple:
    """
    分类日志消息。
    返回 (event_type, phase)
    event_type: event(关键事件) / runtime(运行日志) / raw(原始)
    phase: 当前阶段名
    """
    # 判断是否为关键事件
    event_type = "runtime"
    for kw in _EVENT_KEYWORDS:
        if kw in message:
            event_type = "event"
            break

    # 推断阶段
    phase = ""
    for kw, ph in _PHASE_KEYWORDS.items():
        if kw in message:
            phase = ph
            break

    return event_type, phase


class WSLogHandler(logging.Handler):
    """将日志记录放入 asyncio 队列，由 WS 客户端发送。"""

    def __init__(self, queue: asyncio.Queue, task_id_getter=None, state_getter=None):
        super().__init__()
        self._queue = queue
        self._task_id_getter = task_id_getter
        self._state_getter = state_getter  # 获取当前状态机阶段
        self._loop = None

    def set_loop(self, loop):
        self._loop = loop

    def emit(self, record):
        try:
            task_id = self._task_id_getter() if self._task_id_getter else ""
            msg_text = self.format(record)
            event_type, phase = _classify_log(msg_text)

            # 如果有状态机阶段，优先使用
            if self._state_getter:
                try:
                    machine_state = self._state_getter()
                    if machine_state:
                        phase = machine_state
                except Exception:
                    pass

            message = make_message(MSG_LOG_ENTRY, {
                "task_id": task_id or "",
                "level": record.levelname,
                "event_type": event_type,
                "phase": phase,
                "message": msg_text,
                "timestamp": datetime.now().isoformat(),
            })
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._queue.put_nowait, message)
        except Exception:
            pass
