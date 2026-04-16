"""
运行报告模块 — 每次运行结束后生成结果摘要。
"""
import os
import json
import logging
from datetime import datetime

logger = logging.getLogger("1688-auto")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPORT_DIR = os.path.join(_ROOT, "reports")


def save_report(config: dict, added_count: int, order_count: int, errors: list = None):
    """保存运行报告到 reports/ 目录。"""
    os.makedirs(_REPORT_DIR, exist_ok=True)

    from src.selector_health import get_tracker

    now = datetime.now()
    tracker = get_tracker()
    broken_selectors = tracker.get_broken_groups()

    report = {
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "shop": config.get("search", {}).get("target_shop_name", ""),
        "target_amount": config.get("cart", {}).get("target_amount", 0),
        "order_limit": config.get("cart", {}).get("order_limit", 500),
        "items_added": added_count,
        "orders_created": order_count,
        "errors": errors or [],
        "broken_selectors": broken_selectors,
    }

    filename = now.strftime("report_%Y%m%d_%H%M%S.json")
    filepath = os.path.join(_REPORT_DIR, filename)

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"运行报告已保存: {filepath}")
    except Exception as e:
        logger.warning(f"保存报告失败: {e}")

    # 打印摘要
    print(f"\n{'='*50}")
    print(f"  运行报告")
    print(f"{'='*50}")
    print(f"  时间: {report['time']}")
    print(f"  店铺: {report['shop']}")
    print(f"  加购商品: {added_count} 件")
    print(f"  生成订单: {order_count} 笔")
    if errors:
        print(f"  异常: {len(errors)} 条")
    print(f"  报告文件: {filepath}")
    print(f"{'='*50}\n")

    return filepath
