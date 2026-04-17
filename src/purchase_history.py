"""
采购去重模块 — 记录已采购商品，避免重复购买。

商品唯一标识：店铺名 + offerId（从详情页URL提取）
标记时机：订单提交成功后
存储位置：项目根目录 data/purchased.json
"""
import os
import json
import re
import logging
from datetime import datetime

logger = logging.getLogger("1688-auto")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_ROOT, "data")
_HISTORY_FILE = os.path.join(_DATA_DIR, "purchased.json")

# 内存缓存
_purchased = set()
_loaded = False


def _load():
    """从文件加载已采购记录。"""
    global _purchased, _loaded
    if _loaded:
        return
    os.makedirs(_DATA_DIR, exist_ok=True)
    if os.path.isfile(_HISTORY_FILE):
        try:
            with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            _purchased = set(data.get("items", []))
            logger.info(f"已加载 {len(_purchased)} 条采购历史记录")
        except Exception as e:
            logger.warning(f"加载采购历史失败: {e}")
            _purchased = set()
    _loaded = True


def _save():
    """保存到文件。"""
    os.makedirs(_DATA_DIR, exist_ok=True)
    try:
        with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "items": list(_purchased),
                "count": len(_purchased),
                "updated_at": datetime.now().isoformat(),
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存采购历史失败: {e}")


def extract_offer_id(url: str) -> str:
    """从商品详情页URL提取 offerId。"""
    # https://detail.1688.com/offer/1234567890.html
    match = re.search(r'/offer/(\d+)', url)
    if match:
        return match.group(1)
    # URL参数形式
    match = re.search(r'offerId=(\d+)', url)
    if match:
        return match.group(1)
    return ""


def make_product_key(shop_name: str, offer_id: str) -> str:
    """生成商品唯一标识。"""
    return f"{shop_name}|{offer_id}"


def is_purchased(shop_name: str, offer_id: str) -> bool:
    """检查商品是否已采购过。"""
    _load()
    key = make_product_key(shop_name, offer_id)
    return key in _purchased


def mark_purchased(shop_name: str, offer_id: str):
    """标记商品为已采购。"""
    _load()
    key = make_product_key(shop_name, offer_id)
    if key not in _purchased:
        _purchased.add(key)
        _save()
        logger.info(f"已标记采购: {key}")


def mark_batch_purchased(shop_name: str, offer_ids: list):
    """批量标记已采购。"""
    _load()
    added = 0
    for oid in offer_ids:
        key = make_product_key(shop_name, oid)
        if key not in _purchased:
            _purchased.add(key)
            added += 1
    if added > 0:
        _save()
        logger.info(f"批量标记 {added} 个商品为已采购")


def get_purchased_count() -> int:
    """获取已采购商品总数。"""
    _load()
    return len(_purchased)


def clear_history(shop_name: str = None):
    """清除采购历史（可指定店铺）。"""
    global _purchased
    _load()
    if shop_name:
        prefix = f"{shop_name}|"
        _purchased = {k for k in _purchased if not k.startswith(prefix)}
    else:
        _purchased = set()
    _save()
    logger.info(f"已清除采购历史{'(' + shop_name + ')' if shop_name else '(全部)'}")
