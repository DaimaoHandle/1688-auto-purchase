"""
日志拦截器 — 捕获 1688-auto logger 的输出，转发到 WebSocket。
"""
import logging
import asyncio
from datetime import datetime
from shared.protocol import make_message, MSG_LOG_ENTRY


class WSLogHandler(logging.Handler):
    """将日志记录放入 asyncio 队列，由 WS 客户端发送。"""

    def __init__(self, queue: asyncio.Queue, task_id_getter=None):
        super().__init__()
        self._queue = queue
        self._task_id_getter = task_id_getter
        self._loop = None

    def set_loop(self, loop):
        self._loop = loop

    def emit(self, record):
        try:
            task_id = self._task_id_getter() if self._task_id_getter else ""
            message = make_message(MSG_LOG_ENTRY, {
                "task_id": task_id or "",
                "level": record.levelname,
                "message": self.format(record),
                "timestamp": datetime.now().isoformat(),
            })
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._queue.put_nowait, message)
        except Exception:
            pass
