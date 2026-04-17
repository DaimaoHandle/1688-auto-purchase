"""
Playwright 工作线程 — 状态机驱动的采购自动化。
每个阶段是独立方法，状态转换明确，支持断点恢复。
"""
import os
import sys
import json
import logging
import threading
import asyncio
import tempfile
import time

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
    状态机驱动的采购工作线程。

    状态流转：
    starting -> waiting_login -> logged_in -> searching ->
    entering_shop -> filling_cart -> cart_filled ->
    awaiting_approval -> checking_out -> completed
    """

    # 状态转换表：当前状态 -> 处理方法 -> 下一状态
    STATE_TRANSITIONS = [
        (STATUS_STARTING,          "_do_starting",          STATUS_WAITING_LOGIN),
        (STATUS_WAITING_LOGIN,     "_do_waiting_login",     STATUS_LOGGED_IN),
        (STATUS_LOGGED_IN,         "_do_logged_in",         STATUS_SEARCHING),
        (STATUS_SEARCHING,         "_do_searching",         STATUS_ENTERING_SHOP),
        (STATUS_ENTERING_SHOP,     "_do_entering_shop",     STATUS_FILLING_CART),
        (STATUS_FILLING_CART,      "_do_filling_cart",       STATUS_CART_FILLED),
        (STATUS_CART_FILLED,       "_do_cart_filled",        STATUS_AWAITING_APPROVAL),
        (STATUS_AWAITING_APPROVAL, "_do_awaiting_approval", STATUS_CHECKING_OUT),
        (STATUS_CHECKING_OUT,      "_do_checking_out",      STATUS_COMPLETED),
    ]

    def __init__(self, out_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self._out_queue = out_queue
        self._loop = loop
        self._thread = None
        self._cancel_event = threading.Event()
        self._approve_event = threading.Event()
        self._approved = False
        self._task_id = ""
        self._running = False
        self._context = None
        self._state = None

        # 任务上下文（各阶段共享数据）
        self._ctx = {}

    @property
    def running(self) -> bool:
        return self._running

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def state(self) -> str:
        return self._state

    def start_task(self, task_id: str, config: dict, image_data: bytes = None, image_filename: str = ""):
        if self._running:
            logger.warning("已有任务在运行")
            return
        self._task_id = task_id
        self._cancel_event.clear()
        self._approve_event.clear()
        self._approved = False
        self._running = True
        self._context = None
        self._state = STATUS_STARTING
        self._ctx = {
            "config": config,
            "image_data": image_data,
            "image_filename": image_filename,
            "added": 0,
            "orders": 0,
            "actual_amount": 0.0,
            "errors": [],
            "image_path": None,
            "playwright": None,
            "browser_context": None,
            "page": None,
            "result_page": None,
            "shop_page": None,
        }
        self._thread = threading.Thread(target=self._run_state_machine, daemon=True)
        self._thread.start()

    def stop_task(self):
        self._cancel_event.set()
        self._approve_event.set()
        if self._context:
            try:
                self._context.close()
                logger.info("已强制关闭浏览器")
            except Exception:
                pass

    def approve_checkout(self):
        self._approved = True
        self._approve_event.set()

    def reject_checkout(self):
        self._approved = False
        self._approve_event.set()

    # ─── 通信 ─────────────────────────────────────────────

    def _send(self, message: str):
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._out_queue.put_nowait, message)

    def _send_status(self, status: str, message: str = ""):
        self._state = status
        self._send(make_message(MSG_STATUS_UPDATE, {
            "task_id": self._task_id, "status": status, "message": message,
        }))

    def _send_progress(self, added, price, amount, target, page_num):
        self._send(make_message(MSG_PROGRESS_UPDATE, {
            "task_id": self._task_id, "items_added": added,
            "item_price": price, "local_amount": amount,
            "target_amount": target, "current_page": page_num,
        }))

    def _is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # ─── 状态机引擎 ───────────────────────────────────────

    def _run_state_machine(self):
        """状态机主循环：按转换表依次执行每个阶段。"""
        try:
            for state, handler_name, next_state in self.STATE_TRANSITIONS:
                if self._is_cancelled():
                    self._send_status(STATUS_CANCELLED)
                    return

                self._state = state
                handler = getattr(self, handler_name)

                logger.info(f"[状态机] {state} -> {handler_name}")
                result = handler()

                # handler 返回 False 表示流程应终止（如用户拒绝结算）
                if result is False:
                    return

            # 所有阶段完成
            c = self._ctx
            self._send_status(STATUS_COMPLETED,
                f"完成！{c['added']} 件商品，{c['orders']} 笔订单，¥{c['actual_amount']:.2f}")

        except Exception as e:
            logger.error(f"任务异常: {e}", exc_info=True)
            self._ctx["errors"].append(str(e))
            self._send_status(STATUS_FAILED, str(e))

        finally:
            self._cleanup()

    def _cleanup(self):
        """清理资源，发送最终报告。"""
        c = self._ctx
        config = c.get("config", {})

        # 发送最终报告
        self._send(make_message(MSG_TASK_REPORT, {
            "task_id": self._task_id,
            "shop_name": config.get("search", {}).get("target_shop_name", ""),
            "target_amount": config.get("cart", {}).get("target_amount", 0),
            "actual_amount": c.get("actual_amount", 0),
            "items_added": c.get("added", 0),
            "orders_created": c.get("orders", 0),
            "errors": c.get("errors", []),
        }))

        # 清理临时图片
        image_path = c.get("image_path")
        if image_path and os.path.isfile(image_path):
            try:
                os.unlink(image_path)
            except Exception:
                pass

        # 关闭浏览器
        self._context = None
        ctx = c.get("browser_context")
        if ctx:
            try:
                ctx.close()
            except Exception:
                pass
        pw = c.get("playwright")
        if pw:
            try:
                pw.stop()
            except Exception:
                pass

        self._running = False

    # ─── 各阶段处理方法 ───────────────────────────────────

    def _do_starting(self):
        """初始化：配置合并、图片准备、浏览器启动。"""
        self._send_status(STATUS_STARTING, "正在启动...")
        c = self._ctx
        config = c["config"]

        # 设置采购历史 API
        try:
            from src.purchase_history import set_server_url
            agent_cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_config.json")
            if os.path.isfile(agent_cfg_path):
                with open(agent_cfg_path, "r") as f:
                    acfg = json.load(f)
                set_server_url(acfg.get("server_url", ""))
        except Exception:
            pass

        # 配置兜底
        if not config or not config.get("search"):
            local_cfg_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"
            )
            if os.path.isfile(local_cfg_path):
                with open(local_cfg_path, "r", encoding="utf-8") as f:
                    local_config = json.load(f)
                for key, val in local_config.items():
                    if key not in config or not config[key]:
                        config[key] = val
                logger.info("使用本地 config.json 补齐配置")

        # 准备搜索图片
        if c["image_data"]:
            suffix = os.path.splitext(c["image_filename"])[1] or ".png"
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            tmp.write(c["image_data"])
            tmp.close()
            c["image_path"] = tmp.name
            config.setdefault("search", {})["image_path"] = tmp.name

        # 初始化日志
        from src.utils import setup_logging
        setup_logging(config)

        # 启动浏览器
        from src.browser import init_browser
        playwright_obj, browser, context, page = init_browser(config)
        c["playwright"] = playwright_obj
        c["browser_context"] = context
        c["page"] = page
        self._context = context

    def _do_waiting_login(self):
        """等待用户手动登录 1688。"""
        self._send_status(STATUS_WAITING_LOGIN, "请在浏览器中手动登录 1688")
        c = self._ctx
        config = c["config"]
        page = c["page"]

        from src.login import is_logged_in
        timeout_ms = config.get("timeouts", {}).get("login_wait", 120000)
        deadline = time.time() + timeout_ms / 1000

        while not is_logged_in(page):
            if self._is_cancelled():
                self._send_status(STATUS_CANCELLED)
                return False
            if time.time() > deadline:
                raise TimeoutError("登录等待超时")
            page.wait_for_timeout(2000)

    def _do_logged_in(self):
        """登录成功，获取采购车 URL。"""
        self._send_status(STATUS_LOGGED_IN, "登录成功")
        c = self._ctx
        page = c["page"]

        from src.cart import capture_cart_url, set_cart_url
        cart_url = capture_cart_url(page)
        if cart_url:
            set_cart_url(cart_url)

    def _do_searching(self):
        """以图搜图。"""
        self._send_status(STATUS_SEARCHING, "以图搜图中...")
        c = self._ctx
        config = c["config"]
        context = c["browser_context"]
        page = c["page"]

        from src.search import image_search
        search_image = config.get("search", {}).get("image_path", "")
        c["result_page"] = image_search(context, page, search_image)

    def _do_entering_shop(self):
        """定位目标店铺，进入全店商品列表。"""
        self._send_status(STATUS_ENTERING_SHOP, "定位目标店铺...")
        c = self._ctx
        config = c["config"]
        context = c["browser_context"]

        from src.shop import find_shop_and_enter
        shop_name = config.get("search", {}).get("target_shop_name", "")
        c["shop_page"] = find_shop_and_enter(context, c["result_page"], shop_name)

    def _do_filling_cart(self):
        """加购商品。"""
        self._send_status(STATUS_FILLING_CART, "开始加购...")
        c = self._ctx
        config = c["config"]
        context = c["browser_context"]

        from src.cart import run_cart_filling
        cart_cfg = config.get("cart", {})
        cart_cfg["_shop_name"] = config.get("search", {}).get("target_shop_name", "")

        c["added"] = run_cart_filling(
            context, c["shop_page"], cart_cfg,
            progress_callback=self._send_progress,
            cancel_check=self._is_cancelled,
        )

    def _do_cart_filled(self):
        """加购完成，准备请求审批。"""
        c = self._ctx
        self._send_status(STATUS_CART_FILLED, f"加购完成，共 {c['added']} 件商品")

    def _do_awaiting_approval(self):
        """等待管理面板确认结算。"""
        self._send_status(STATUS_AWAITING_APPROVAL, "等待确认结算")
        self._approve_event.wait()

        if self._is_cancelled():
            self._send_status(STATUS_CANCELLED)
            return False

        if not self._approved:
            self._send_status(STATUS_COMPLETED, "用户选择不结算")
            return False

    def _do_checking_out(self):
        """结算下单。"""
        self._send_status(STATUS_CHECKING_OUT, "开始结算...")
        c = self._ctx
        config = c["config"]
        context = c["browser_context"]

        from src.cart import run_cart_checkout
        order_limit = config.get("cart", {}).get("order_limit", 500)
        shipping_reserve = config.get("cart", {}).get("shipping_reserve", 15)
        shop_name = config.get("search", {}).get("target_shop_name", "")

        orders, actual_amount = run_cart_checkout(
            context, order_limit=order_limit,
            shipping_reserve=shipping_reserve, shop_name=shop_name
        )
        c["orders"] = orders
        c["actual_amount"] = actual_amount
