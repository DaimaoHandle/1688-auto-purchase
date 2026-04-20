"""
Agent 入口 — 采购服务器上运行，连接管理服务器，接收指令执行采购任务。

用法：python3 agent/agent_main.py
"""
import sys
import os
import json
import asyncio
import logging

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.ws_client import AgentWSClient
from agent.worker import PurchaseWorker
from agent.log_interceptor import WSLogHandler
from shared.protocol import (
    MSG_START_TASK, MSG_STOP_TASK, MSG_APPROVE_CHECKOUT, MSG_REJECT_CHECKOUT, MSG_UPDATE_CODE,
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("agent")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_config.json")


def load_agent_config() -> dict:
    if not os.path.isfile(CONFIG_PATH):
        logger.error(f"配置文件不存在: {CONFIG_PATH}")
        logger.error("请先在管理面板添加节点，获取 node_id 和 token，填入 agent_config.json")
        sys.exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    if not config.get("node_id") or not config.get("token"):
        logger.error("agent_config.json 中 node_id 和 token 不能为空")
        sys.exit(1)

    return config


async def main():
    config = load_agent_config()
    server_url = config["server_url"]
    node_id = config["node_id"]
    token = config["token"]

    print(f"\n{'='*50}")
    print(f"  1688 采购 Agent")
    print(f"  节点ID: {node_id}")
    print(f"  管理服务器: {server_url}")
    print(f"{'='*50}\n")

    loop = asyncio.get_event_loop()
    client = AgentWSClient(server_url, node_id, token)
    worker = PurchaseWorker(client.outgoing, loop)

    # 安装日志拦截器：捕获 1688-auto logger 的输出转发到 WS
    log_handler = WSLogHandler(client.outgoing, task_id_getter=lambda: worker.task_id, state_getter=lambda: worker.state)
    log_handler.set_loop(loop)
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    purchase_logger = logging.getLogger("1688-auto")
    # 清除已有的 WSLogHandler（防止重启后重复）
    purchase_logger.handlers = [h for h in purchase_logger.handlers if not isinstance(h, WSLogHandler)]
    purchase_logger.addHandler(log_handler)

    # 命令处理循环
    async def command_loop():
        while True:
            try:
                msg = await client.incoming.get()
                msg_type = msg["type"]
                payload = msg["payload"]

                if msg_type == MSG_START_TASK:
                    task_id = payload.get("task_id", "")
                    task_config = payload.get("config", {})
                    # 图片通过 HTTP 下载（如果有 image_url）
                    image_data = None
                    image_filename = ""
                    image_url = payload.get("image_url", "")
                    if image_url:
                        try:
                            import urllib.request
                            req = urllib.request.Request(image_url)
                            with urllib.request.urlopen(req, timeout=30) as resp:
                                image_data = resp.read()
                            image_filename = payload.get("image_filename", "search.png")
                            logger.info(f"已下载搜索图片: {len(image_data)} 字节")
                        except Exception as e:
                            logger.warning(f"下载图片失败: {e}")

                    logger.info(f"收到启动任务指令: {task_id}")
                    worker.start_task(task_id, task_config, image_data, image_filename)

                elif msg_type == MSG_STOP_TASK:
                    logger.info("收到停止任务指令")
                    worker.stop_task()

                elif msg_type == MSG_APPROVE_CHECKOUT:
                    logger.info("收到批准结算指令")
                    worker.approve_checkout()

                elif msg_type == MSG_REJECT_CHECKOUT:
                    logger.info("收到拒绝结算指令")
                    worker.reject_checkout()

                elif msg_type == MSG_UPDATE_CODE:
                    logger.info("收到代码更新指令，执行 git pull...")
                    import subprocess
                    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    result = subprocess.run(
                        ["git", "pull"], cwd=project_root,
                        capture_output=True, text=True, timeout=30
                    )
                    logger.info(f"git pull: {result.stdout.strip()}")
                    if result.returncode == 0:
                        from shared.protocol import make_message, MSG_STATUS_UPDATE
                        await client.send(make_message(MSG_STATUS_UPDATE, {
                            "task_id": "", "status": "updated",
                            "message": f"代码已更新: {result.stdout.strip()}，正在重启..."
                        }))
                        await asyncio.sleep(1)
                        # 重启自身
                        logger.info("正在重启 Agent...")
                        await client.stop()
                        os.execv(sys.executable, [sys.executable] + sys.argv)
                    else:
                        logger.warning(f"git pull 失败: {result.stderr.strip()}")
                        from shared.protocol import make_message, MSG_STATUS_UPDATE
                        await client.send(make_message(MSG_STATUS_UPDATE, {
                            "task_id": "", "status": "updated",
                            "message": f"更新失败: {result.stderr.strip()}"
                        }))

                else:
                    logger.debug(f"未知消息类型: {msg_type}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"命令处理异常: {e}")

    try:
        # 并发运行 WS 客户端和命令处理
        await asyncio.gather(
            client.start(),
            command_loop(),
        )
    except KeyboardInterrupt:
        logger.info("用户中断")
    finally:
        await client.stop()


if __name__ == "__main__":
    asyncio.run(main())
