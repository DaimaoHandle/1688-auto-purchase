"""
Agent WebSocket 客户端 — 连接到管理服务器，自动重连，收发消息。
"""
import asyncio
import logging
import time
import random

logger = logging.getLogger("agent")

try:
    import websockets
except ImportError:
    websockets = None
    logger.error("websockets 未安装，请执行: pip install websockets")


class AgentWSClient:
    """WebSocket 客户端，自动重连，心跳保活。"""

    def __init__(self, server_url: str, node_id: str, token: str):
        self.server_url = server_url
        self.node_id = node_id
        self.token = token
        self._ws = None
        self._connected = False
        self._running = False
        # 收到的消息队列（Server → Agent 的指令）
        self.incoming = asyncio.Queue()
        # 待发送的消息队列（Agent → Server 的上报）
        self.outgoing = asyncio.Queue()

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self):
        """启动客户端（自动重连循环）。"""
        if not websockets:
            logger.error("websockets 库未安装")
            return

        self._running = True
        backoff = 1.0

        while self._running:
            try:
                logger.info(f"连接管理服务器: {self.server_url}")
                async with websockets.connect(
                    self.server_url,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=10 * 1024 * 1024,  # 10MB
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    backoff = 1.0  # 重置退避
                    logger.info("已连接到管理服务器")

                    # 发送注册消息
                    from shared.protocol import make_message, MSG_REGISTER
                    await ws.send(make_message(MSG_REGISTER, {
                        "node_id": self.node_id,
                        "token": self.token,
                    }))

                    # 并发：发送、接收、心跳
                    await asyncio.gather(
                        self._recv_loop(ws),
                        self._send_loop(ws),
                        self._heartbeat_loop(ws),
                    )

            except Exception as e:
                self._connected = False
                self._ws = None
                if self._running:
                    jitter = random.uniform(0, backoff * 0.5)
                    wait = min(backoff + jitter, 60.0)
                    logger.warning(f"连接断开: {e}，{wait:.1f}秒后重连...")
                    await asyncio.sleep(wait)
                    backoff = min(backoff * 2, 60.0)

    async def stop(self):
        """停止客户端。"""
        self._running = False
        if self._ws:
            await self._ws.close()

    async def send(self, message: str):
        """将消息放入发送队列。"""
        await self.outgoing.put(message)

    async def _recv_loop(self, ws):
        """接收消息循环。"""
        async for raw in ws:
            from shared.protocol import parse_message, MSG_REGISTER_ACK, MSG_HEARTBEAT_ACK
            msg = parse_message(raw)
            if msg["type"] == MSG_REGISTER_ACK:
                payload = msg["payload"]
                if payload.get("ok"):
                    logger.info(f"注册成功，节点名: {payload.get('node_name', '')}")
                else:
                    logger.error(f"注册失败: {payload.get('error', '未知错误')}")
                    self._running = False
                    return
            elif msg["type"] == MSG_HEARTBEAT_ACK:
                pass  # 心跳回复，忽略
            else:
                # 其他消息（任务指令）放入接收队列
                await self.incoming.put(msg)

    async def _send_loop(self, ws):
        """发送消息循环。"""
        while self._running:
            try:
                message = await asyncio.wait_for(self.outgoing.get(), timeout=1.0)
                await ws.send(message)
            except asyncio.TimeoutError:
                continue

    async def _heartbeat_loop(self, ws):
        """心跳循环，每15秒发一次。"""
        from shared.protocol import make_message, MSG_HEARTBEAT
        while self._running:
            await asyncio.sleep(15)
            try:
                await ws.send(make_message(MSG_HEARTBEAT, {
                    "timestamp": time.time(),
                }))
            except Exception:
                return  # 连接断了，退出心跳
