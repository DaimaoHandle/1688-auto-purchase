"""
Playwright 工作线程 — 在独立线程中运行采购自动化。
通过队列与 asyncio 主线程通信。
"""
import os
import sys
import json
import logging
import threading
import asyncio
import tempfile
import base64

# 确保项目根目录在 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.protocol import (
    make_message,
    MSG_STATUS_UPDATE, MSG_PROGRESS_UPDATE, MSG_TASK_REPORT,
    STATUS_STARTING, STATUS_WAITING_LOGIN, STATUS_LOGGED_IN,
    STATUS_SEARCHING, STATUS_ENTERING_SHOP, STATUS_FILLING_CART,
    STATUS_CART_FILLED, STATUS_AWAITING_APPROVAL, STATUS_CHECKING_OUT,
    STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED,
)

logger = logging.getLogger("1688-auto")


class PurchaseWorker:
    """
    采购工作线程。封装 main.py 的完整流程，通过队列通信。
    """

    def __init__(self, out_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self._out_queue = out_queue
        self._loop = loop
        self._thread = None
        self._cancel_event = threading.Event()
        self._approve_event = threading.Event()
        self._approved = False  # True=批准结算, False=拒绝
        self._task_id = ""
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def task_id(self) -> str:
        return self._task_id

    def start_task(self, task_id: str, config: dict, image_data: bytes = None, image_filename: str = ""):
        """启动采购任务（在新线程中）。"""
        if self._running:
            logger.warning("已有任务在运行")
            return

        self._task_id = task_id
        self._cancel_event.clear()
        self._approve_event.clear()
        self._approved = False
        self._running = True
        self._context = None  # Playwright browser context，由 Worker 线程设置

        self._thread = threading.Thread(
            target=self._run,
            args=(task_id, config, image_data, image_filename),
            daemon=True,
        )
        self._thread.start()

    def stop_task(self):
        """立即取消当前任务：关闭浏览器强制中断 Playwright 操作。"""
        self._cancel_event.set()
        # 唤醒可能在等待结算审批的阻塞
        self._approve_event.set()
        # 直接关闭浏览器，Playwright 正在执行的操作会立即抛异常
        if self._context:
            try:
                self._context.close()
                logger.info("已强制关闭浏览器")
            except Exception:
                pass

    def approve_checkout(self):
        """批准结算。"""
        self._approved = True
        self._approve_event.set()

    def reject_checkout(self):
        """拒绝结算。"""
        self._approved = False
        self._approve_event.set()

    def _send(self, message: str):
        """线程安全地发送消息到 asyncio 队列。"""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._out_queue.put_nowait, message)

    def _send_status(self, status: str, message: str = ""):
        self._send(make_message(MSG_STATUS_UPDATE, {
            "task_id": self._task_id,
            "status": status,
            "message": message,
        }))

    def _send_progress(self, added, price, amount, target, page_num):
        self._send(make_message(MSG_PROGRESS_UPDATE, {
            "task_id": self._task_id,
            "items_added": added,
            "item_price": price,
            "local_amount": amount,
            "target_amount": target,
            "current_page": page_num,
        }))

    def _is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _run(self, task_id: str, config: dict, image_data: bytes, image_filename: str):
        """在工作线程中执行完整采购流程。"""
        playwright_obj = context = page = None
        added = 0
        orders = 0
        actual_amount = 0.0
        errors = []
        image_path = None

        try:
            self._send_status(STATUS_STARTING, "正在启动浏览器...")

            # 设置采购历史 API 地址（从 agent_config 的 server_url 推导）
            try:
                from src.purchase_history import set_server_url
                agent_cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_config.json")
                if os.path.isfile(agent_cfg_path):
                    import json as _json2
                    with open(agent_cfg_path, "r") as _f2:
                        _acfg = _json2.load(_f2)
                    set_server_url(_acfg.get("server_url", ""))
            except Exception:
                pass

            # 如果下发的 config 为空，用本地 config.json 兜底
            if not config or not config.get("search"):
                local_config_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"
                )
                if os.path.isfile(local_config_path):
                    import json as _json
                    with open(local_config_path, "r", encoding="utf-8") as _f:
                        local_config = _json.load(_f)
                    # 合并：下发的 config 优先，缺失项用本地补齐
                    for key, val in local_config.items():
                        if key not in config or not config[key]:
                            config[key] = val
                    logger.info("使用本地 config.json 补齐配置")

            # 准备搜索图片
            if image_data:
                suffix = os.path.splitext(image_filename)[1] or ".png"
                tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                tmp.write(image_data)
                tmp.close()
                image_path = tmp.name
                config.setdefault("search", {})["image_path"] = image_path

            # 写入临时 config 供模块使用
            from src.utils import setup_logging
            setup_logging(config)

            from src.browser import init_browser
            from src.login import wait_for_login
            from src.search import image_search
            from src.shop import find_shop_and_enter
            from src.cart import run_cart_filling, run_cart_checkout, capture_cart_url, set_cart_url

            # 初始化浏览器
            playwright_obj, browser, context, page = init_browser(config)
            self._context = context  # 保存引用，stop_task 时可直接关闭

            if self._is_cancelled():
                self._send_status(STATUS_CANCELLED)
                return

            # 等待登录
            self._send_status(STATUS_WAITING_LOGIN, "请在浏览器中手动登录 1688")
            login_timeout = config.get("timeouts", {}).get("login_wait", 120000)
            # 分段等待登录，每 2 秒检查取消
            from src.login import is_logged_in
            import time
            deadline = time.time() + login_timeout / 1000
            while not is_logged_in(page):
                if self._is_cancelled():
                    self._send_status(STATUS_CANCELLED)
                    return
                if time.time() > deadline:
                    raise TimeoutError("登录等待超时")
                page.wait_for_timeout(2000)

            self._send_status(STATUS_LOGGED_IN, "登录成功")

            if self._is_cancelled():
                self._send_status(STATUS_CANCELLED)
                return

            # 获取采购车 URL
            cart_url = capture_cart_url(page)
            if cart_url:
                set_cart_url(cart_url)

            # 搜图
            self._send_status(STATUS_SEARCHING, "以图搜图中...")
            search_image = config.get("search", {}).get("image_path", "")
            result_page = image_search(context, page, search_image)

            if self._is_cancelled():
                self._send_status(STATUS_CANCELLED)
                return

            # 找店铺
            self._send_status(STATUS_ENTERING_SHOP, "定位目标店铺...")
            shop_name = config.get("search", {}).get("target_shop_name", "")
            shop_page = find_shop_and_enter(context, result_page, shop_name)

            if self._is_cancelled():
                self._send_status(STATUS_CANCELLED)
                return

            # 加购（注入 shop_name 用于采购去重）
            self._send_status(STATUS_FILLING_CART, "开始加购...")
            cart_cfg = config.get("cart", {})
            cart_cfg["_shop_name"] = config.get("search", {}).get("target_shop_name", "")
            added = run_cart_filling(
                context, shop_page, cart_cfg,
                progress_callback=self._send_progress,
                cancel_check=self._is_cancelled,
            )

            self._send_status(STATUS_CART_FILLED, f"加购完成，共 {added} 件商品")

            if self._is_cancelled():
                self._send_status(STATUS_CANCELLED)
                return

            # 等待结算审批
            self._send_status(STATUS_AWAITING_APPROVAL, "等待确认结算")
            self._approve_event.wait()  # 阻塞直到管理面板批准或拒绝

            if self._is_cancelled():
                self._send_status(STATUS_CANCELLED)
                return

            if not self._approved:
                self._send_status(STATUS_COMPLETED, "用户选择不结算")
                return

            # 结算
            self._send_status(STATUS_CHECKING_OUT, "开始结算...")
            order_limit = config.get("cart", {}).get("order_limit", 500)
            shipping_reserve = config.get("cart", {}).get("shipping_reserve", 15)
            _shop_name = config.get("search", {}).get("target_shop_name", "")
            orders, actual_amount = run_cart_checkout(context, order_limit=order_limit, shipping_reserve=shipping_reserve, shop_name=_shop_name)

            self._send_status(STATUS_COMPLETED, f"完成！{added} 件商品，{orders} 笔订单，¥{actual_amount:.2f}")

        except Exception as e:
            logger.error(f"任务异常: {e}", exc_info=True)
            errors.append(str(e))
            self._send_status(STATUS_FAILED, str(e))
        finally:
            # 发送最终报告
            self._send(make_message(MSG_TASK_REPORT, {
                "task_id": task_id,
                "shop_name": config.get("search", {}).get("target_shop_name", ""),
                "target_amount": config.get("cart", {}).get("target_amount", 0),
                "actual_amount": actual_amount,
                "items_added": added,
                "orders_created": orders,
                "errors": errors,
            }))

            # 清理临时图片
            if image_path and os.path.isfile(image_path):
                try:
                    os.unlink(image_path)
                except Exception:
                    pass

            # 关闭浏览器（可能已被 stop_task 关闭）
            self._context = None
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            if playwright_obj:
                try:
                    playwright_obj.stop()
                except Exception:
                    pass

            self._running = False
