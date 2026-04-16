"""
Agent 入口 — 采购服务器上运行，连接到管理服务器。

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

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agent")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_config.json")


def load_agent_config() -> dict:
    """加载 Agent 配置。"""
    if not os.path.isfile(CONFIG_PATH):
        logger.error(f"配置文件不存在: {CONFIG_PATH}")
        logger.error("请先在管理面板添加节点，获取 node_id 和 token，填入 agent_config.json")
        sys.exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    if not config.get("node_id") or not config.get("token"):
        logger.error("agent_config.json 中 node_id 和 token 不能为空")
        logger.error("请在管理面板添加节点后，将生成的 node_id 和 token 填入配置")
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

    client = AgentWSClient(server_url, node_id, token)

    try:
        await client.start()
    except KeyboardInterrupt:
        logger.info("用户中断")
    finally:
        await client.stop()


if __name__ == "__main__":
    asyncio.run(main())
