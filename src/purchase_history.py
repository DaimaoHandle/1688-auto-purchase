"""
采购去重模块 — 通过管理端 API 查询和标记已采购商品。

所有采购端共享同一份去重记录（存储在管理端数据库）。
Agent 通过 HTTP API 与管理端通信。
"""
import re
import logging
import urllib.request
import json

logger = logging.getLogger("1688-auto")

# 管理端 API 地址（从 agent_config 或 cart_url 获取）
_server_base_url = ""


def set_server_url(url: str):
    """设置管理端 HTTP 地址（由 Agent 启动时调用）。"""
    global _server_base_url
    # 从 ws:// 转为 http://
    _server_base_url = url.replace("ws://", "http://").replace("wss://", "https://")
    # 去掉路径部分（如 /ws/agent）
    parts = _server_base_url.split("/")
    if len(parts) >= 3:
        _server_base_url = "/".join(parts[:3])
    logger.info(f"采购历史API: {_server_base_url}")


def extract_offer_id(url: str) -> str:
    """从商品详情页 URL 提取 offerId。"""
    match = re.search(r'/offer/(\d+)', url)
    if match:
        return match.group(1)
    match = re.search(r'offerId=(\d+)', url)
    if match:
        return match.group(1)
    return ""


def extract_offer_id_from_href(href: str) -> str:
    """从商品链接 href 提取 offerId。"""
    return extract_offer_id(href)


def check_purchased_batch(shop_name: str, offer_ids: list) -> set:
    """
    批量检查哪些 offerId 已采购过。
    返回已采购的 offerId 集合。
    """
    if not _server_base_url or not offer_ids or not shop_name:
        return set()

    try:
        data = json.dumps({"shop_name": shop_name, "offer_ids": offer_ids}).encode("utf-8")
        req = urllib.request.Request(
            f"{_server_base_url}/api/purchased/check",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return set(result.get("purchased", []))
    except Exception as e:
        logger.warning(f"查询采购历史失败: {e}")
        return set()


def is_purchased(shop_name: str, offer_id: str) -> bool:
    """检查单个商品是否已采购过。"""
    return offer_id in check_purchased_batch(shop_name, [offer_id])


def mark_batch_purchased(shop_name: str, offer_ids: list, node_id: str = ""):
    """批量标记 offerId 为已采购。"""
    if not _server_base_url or not offer_ids or not shop_name:
        return

    try:
        data = json.dumps({"shop_name": shop_name, "offer_ids": offer_ids, "node_id": node_id}).encode("utf-8")
        req = urllib.request.Request(
            f"{_server_base_url}/api/purchased/mark",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            logger.info(f"标记已采购: {result.get('added', 0)} 个新商品")
    except Exception as e:
        logger.warning(f"标记采购历史失败: {e}")


def get_purchased_count(shop_name: str = "") -> int:
    """获取已采购商品总数。"""
    if not _server_base_url:
        return 0
    try:
        url = f"{_server_base_url}/api/purchased/count"
        if shop_name:
            url += f"?shop_name={urllib.parse.quote(shop_name)}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("count", 0)
    except Exception:
        return 0
