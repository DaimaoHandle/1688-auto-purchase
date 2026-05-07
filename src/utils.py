import json
import logging
import os
import re
import time
import random
from pathlib import Path


def load_config(path: str = "config.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def setup_logging(config: dict) -> logging.Logger:
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO"))
    log_file = log_cfg.get("file", "logs/app.log")

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    purchase_logger = logging.getLogger("1688-auto")
    purchase_logger.setLevel(level)
    purchase_logger.propagate = False  # 不传递到 root logger，避免重复

    # 清除已有的非 WS handler（防止重复调用时累加）
    from agent.log_interceptor import WSLogHandler
    purchase_logger.handlers = [h for h in purchase_logger.handlers if isinstance(h, WSLogHandler)]

    # 添加文件和控制台 handler
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    purchase_logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    purchase_logger.addHandler(sh)

    return purchase_logger


def parse_price(text: str) -> float:
    """解析价格字符串，如 '¥1,234.56' -> 1234.56，'1234' -> 1234.0"""
    if not text:
        return 0.0
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def random_delay(min_sec: float = 0.5, max_sec: float = 2.0):
    time.sleep(random.uniform(min_sec, max_sec))


def save_screenshot(page, name: str, folder: str = "logs"):
    Path(folder).mkdir(parents=True, exist_ok=True)
    path = os.path.join(folder, f"{name}_{int(time.time())}.png")
    try:
        page.screenshot(path=path)
        return path
    except Exception:
        return None
