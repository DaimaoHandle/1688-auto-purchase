"""
采购任务状态机 — 可持久化、可恢复、可审计的任务控制核心。

设计原则：
- 状态与资源分离：task_data（可持久化） vs runtime（运行时资源）
- 错误分类：retryable / manual / fatal
- 统一暂停语义：waiting_for 字段描述等待原因
- 每次状态转换记录审计轨迹
- 明确恢复边界：每个状态标记 recoverable 属性
"""
import os
import sys
import json
import logging
import threading
import asyncio
import tempfile
import time
from datetime import datetime

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

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_FILE = os.path.join(_PROJECT_ROOT, "data", "task_state.json")


# ─── 错误分类 ──────────────────────────────────────────

class TaskError(Exception):
    """任务错误基类"""
    error_type = "unknown"

class RetryableError(TaskError):
    """可自动重试的错误（网络超时、页面加载失败等）"""
    error_type = "retryable"

class ManualError(TaskError):
    """需要人工介入的错误（验证码、登录过期等）"""
    error_type = "manual"

class FatalError(TaskError):
    """致命错误，不可恢复（配置错误、店铺不存在等）"""
    error_type = "fatal"


# ─── 状态定义 ──────────────────────────────────────────

class StateInfo:
    """状态元信息"""
    def __init__(self, name, handler, next_state, recoverable=True, description=""):
        self.name = name
        self.handler = handler
        self.next_state = next_state
        self.recoverable = recoverable  # 重启后是否可从此状态恢复
        self.description = description


STATE_DEFS = [
    StateInfo(STATUS_STARTING,          "_do_starting",          STATUS_WAITING_LOGIN, recoverable=False, description="初始化浏览器"),
    StateInfo(STATUS_WAITING_LOGIN,     "_do_waiting_login",     STATUS_LOGGED_IN,     recoverable=True,  description="等待手动登录"),
    StateInfo(STATUS_LOGGED_IN,         "_do_logged_in",         STATUS_SEARCHING,     recoverable=True,  description="获取采购车URL"),
    StateInfo(STATUS_SEARCHING,         "_do_searching",         STATUS_ENTERING_SHOP, recoverable=True,  description="以图搜图"),
    StateInfo(STATUS_ENTERING_SHOP,     "_do_entering_shop",     STATUS_FILLING_CART,   recoverable=True,  description="进入店铺"),
    StateInfo(STATUS_FILLING_CART,      "_do_filling_cart",       STATUS_CART_FILLED,    recoverable=False, description="加购商品"),
    StateInfo(STATUS_CART_FILLED,       "_do_cart_filled",        STATUS_AWAITING_APPROVAL, recoverable=True, description="加购完成"),
    StateInfo(STATUS_AWAITING_APPROVAL, "_do_awaiting_approval", STATUS_CHECKING_OUT,  recoverable=True,  description="等待结算审批"),
    StateInfo(STATUS_CHECKING_OUT,      "_do_checking_out",      STATUS_COMPLETED,     recoverable=False, description="结算下单"),
]


class PurchaseWorker:
    """
    状态机驱动的采购任务执行器。

    task_data: 任务信息（可持久化到磁盘）
      - task_id, config, state, added, orders, actual_amount, errors
      - state_history: 状态转换审计轨迹
      - waiting_for: 当前等待原因（暂停语义）

    runtime: 运行时资源（不可持久化）
      - playwright, context, page, shop_page 等 Playwright 对象
    """

    def __init__(self, out_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self._out_queue = out_queue
        self._loop = loop
        self._thread = None
        self._cancel_event = threading.Event()
        self._approve_event = threading.Event()
        self._approved = False
        self._running = False

        # 任务数据（可持久化）
        self.task_data = {}
        # 运行时资源（不持久化）
        self.runtime = {}

    @property
    def running(self) -> bool:
        return self._running

    @property
    def task_id(self) -> str:
        return self.task_data.get("task_id", "")

    @property
    def state(self) -> str:
        return self.task_data.get("state", "")

    # ─── 外部控制接口 ─────────────────────────────────

    def start_task(self, task_id: str, config: dict, image_data: bytes = None, image_filename: str = ""):
        if self._running:
            logger.warning("已有任务在运行")
            return
        self._cancel_event.clear()
        self._approve_event.clear()
        self._approved = False
        self._running = True

        self.task_data = {
            "task_id": task_id,
            "config": config,
            "state": STATUS_STARTING,
            "added": 0,
            "orders": 0,
            "actual_amount": 0.0,
            "errors": [],
            "waiting_for": None,
            "state_history": [],
            "started_at": datetime.now().isoformat(),
            "image_filename": image_filename,
        }
        self.runtime = {
            "image_data": image_data,
            "image_path": None,
            "playwright": None,
            "context": None,
            "page": None,
            "result_page": None,
            "shop_page": None,
        }
        self._save_state()
        self._thread = threading.Thread(target=self._run_state_machine, daemon=True)
        self._thread.start()

    def stop_task(self):
        self._cancel_event.set()
        self._approve_event.set()
        ctx = self.runtime.get("context")
        if ctx:
            try:
                ctx.close()
                logger.info("已强制关闭浏览器")
            except Exception:
                pass

    def approve_checkout(self):
        self._approved = True
        self._approve_event.set()

    def reject_checkout(self):
        self._approved = False
        self._approve_event.set()

    # ─── 通信 ─────────────────────────────────────────

    def _send(self, message: str):
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._out_queue.put_nowait, message)

    def _send_status(self, status: str, message: str = ""):
        self._send(make_message(MSG_STATUS_UPDATE, {
            "task_id": self.task_data.get("task_id", ""),
            "status": status, "message": message,
            "waiting_for": self.task_data.get("waiting_for"),
        }))

    def _send_progress(self, added, price, amount, target, page_num):
        self._send(make_message(MSG_PROGRESS_UPDATE, {
            "task_id": self.task_data.get("task_id", ""),
            "items_added": added, "item_price": price,
            "local_amount": amount, "target_amount": target,
            "current_page": page_num,
        }))

    def _is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # ─── 状态管理 ──────────────────────────────────────

    def _transition(self, new_state: str, message: str = ""):
        """状态转换：记录审计轨迹，持久化，上报。"""
        old_state = self.task_data.get("state", "")
        self.task_data["state"] = new_state
        self.task_data["waiting_for"] = None

        # 审计轨迹
        self.task_data["state_history"].append({
            "from": old_state,
            "to": new_state,
            "message": message,
            "time": datetime.now().isoformat(),
        })

        self._save_state()
        self._send_status(new_state, message)
        logger.info(f"[状态] {old_state} -> {new_state} | {message}")

    def _set_waiting(self, reason: str):
        """设置暂停等待原因。"""
        self.task_data["waiting_for"] = reason
        self._save_state()
        logger.info(f"[等待] {reason}")

    def _save_state(self):
        """持久化任务状态到磁盘。"""
        try:
            os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
            # 只保存可序列化的 task_data
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.task_data, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.debug(f"保存状态失败: {e}")

    def _clear_state(self):
        """清除持久化的状态文件。"""
        try:
            if os.path.isfile(_STATE_FILE):
                os.unlink(_STATE_FILE)
        except Exception:
            pass

    @staticmethod
    def load_saved_state() -> dict:
        """加载上次保存的任务状态（用于断线恢复判断）。"""
        if os.path.isfile(_STATE_FILE):
            try:
                with open(_STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    # ─── 状态机引擎 ────────────────────────────────────

    def _run_state_machine(self):
        """状态机主循环。"""
        try:
            current = self.task_data["state"]

            # 找到起始状态在转换表中的位置
            start_idx = 0
            for i, sd in enumerate(STATE_DEFS):
                if sd.name == current:
                    start_idx = i
                    break

            # 从当前状态开始依次执行
            for sd in STATE_DEFS[start_idx:]:
                if self._is_cancelled():
                    self._transition(STATUS_CANCELLED, "用户取消")
                    return

                self._transition(sd.name, sd.description)
                handler = getattr(self, sd.handler)

                try:
                    result = handler()
                except RetryableError as e:
                    logger.warning(f"[可重试错误] {sd.name}: {e}")
                    self.task_data["errors"].append({"type": "retryable", "state": sd.name, "message": str(e)})
                    # 可重试错误：记录并继续到失败状态
                    raise
                except ManualError as e:
                    logger.warning(f"[需人工介入] {sd.name}: {e}")
                    self.task_data["errors"].append({"type": "manual", "state": sd.name, "message": str(e)})
                    self._set_waiting(f"需要人工处理: {e}")
                    raise
                except FatalError as e:
                    logger.error(f"[致命错误] {sd.name}: {e}")
                    self.task_data["errors"].append({"type": "fatal", "state": sd.name, "message": str(e)})
                    raise

                # handler 返回 False 表示流程终止（如用户拒绝）
                if result is False:
                    return

            # 全部完成
            td = self.task_data
            self._transition(STATUS_COMPLETED,
                f"完成！{td['added']} 件商品，{td['orders']} 笔订单，¥{td['actual_amount']:.2f}")

        except Exception as e:
            if self.task_data.get("state") != STATUS_CANCELLED:
                error_info = self.task_data.get("errors", [])
                if not any(err.get("message") == str(e) for err in error_info if isinstance(err, dict)):
                    self.task_data.setdefault("errors", []).append({"type": "unknown", "message": str(e)})
                self._transition(STATUS_FAILED, str(e))
                logger.error(f"任务异常: {e}", exc_info=True)

        finally:
            self._cleanup()

    def _cleanup(self):
        """清理资源，发送最终报告。"""
        td = self.task_data
        config = td.get("config", {})
        td["finished_at"] = datetime.now().isoformat()

        # 序列化 errors（确保都是可序列化的）
        errors = []
        for err in td.get("errors", []):
            if isinstance(err, dict):
                errors.append(err)
            else:
                errors.append({"type": "unknown", "message": str(err)})

        self._send(make_message(MSG_TASK_REPORT, {
            "task_id": td.get("task_id", ""),
            "shop_name": config.get("search", {}).get("target_shop_name", ""),
            "target_amount": config.get("cart", {}).get("target_amount", 0),
            "actual_amount": td.get("actual_amount", 0),
            "items_added": td.get("added", 0),
            "orders_created": td.get("orders", 0),
            "errors": errors,
            "state_history": td.get("state_history", []),
        }))

        # 清理临时图片
        image_path = self.runtime.get("image_path")
        if image_path and os.path.isfile(image_path):
            try:
                os.unlink(image_path)
            except Exception:
                pass

        # 关闭浏览器
        ctx = self.runtime.get("context")
        if ctx:
            try:
                ctx.close()
            except Exception:
                pass
        pw = self.runtime.get("playwright")
        if pw:
            try:
                pw.stop()
            except Exception:
                pass

        self.runtime = {}
        self._clear_state()
        self._running = False

    # ─── 各阶段处理方法 ────────────────────────────────

    def _do_starting(self):
        """初始化：配置合并、图片准备、浏览器启动。"""
        td = self.task_data
        config = td["config"]

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
            local_cfg_path = os.path.join(_PROJECT_ROOT, "config.json")
            if os.path.isfile(local_cfg_path):
                with open(local_cfg_path, "r", encoding="utf-8") as f:
                    local_config = json.load(f)
                for key, val in local_config.items():
                    if key not in config or not config[key]:
                        config[key] = val
                logger.info("使用本地 config.json 补齐配置")

        # 准备搜索图片
        image_data = self.runtime.get("image_data")
        if image_data:
            suffix = os.path.splitext(td.get("image_filename", ""))[1] or ".png"
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            tmp.write(image_data)
            tmp.close()
            self.runtime["image_path"] = tmp.name
            config.setdefault("search", {})["image_path"] = tmp.name

        from src.utils import setup_logging
        setup_logging(config)

        # 启动浏览器
        from src.browser import init_browser
        pw, browser, context, page = init_browser(config)
        self.runtime["playwright"] = pw
        self.runtime["context"] = context
        self.runtime["page"] = page

    def _do_waiting_login(self):
        """等待用户手动登录。"""
        self._set_waiting("等待用户在浏览器中登录 1688")
        config = self.task_data["config"]
        page = self.runtime["page"]

        from src.login import is_logged_in
        timeout_ms = config.get("timeouts", {}).get("login_wait", 120000)
        deadline = time.time() + timeout_ms / 1000

        while not is_logged_in(page):
            if self._is_cancelled():
                return False
            if time.time() > deadline:
                raise ManualError("登录等待超时，请检查浏览器")
            page.wait_for_timeout(2000)

    def _do_logged_in(self):
        """获取采购车 URL，检查并清空残留商品。"""
        page = self.runtime["page"]
        context = self.runtime["context"]
        from src.cart import capture_cart_url, set_cart_url, _open_cart_in_new_tab, _mouse_click_select_all, _read_bottom_bar_amount, _confirm_popup

        cart_url = capture_cart_url(page)
        if cart_url:
            set_cart_url(cart_url)

        # 检查采购车是否有残留商品，如果有则清空（无论是否获取到 URL 都尝试）
        logger.info("检查采购车是否有残留商品...")
        cart_page = None
        try:
            cart_page = _open_cart_in_new_tab(context)
            if cart_page:
                cart_page.wait_for_timeout(3000)

                # 先用真实鼠标点击全选
                coord = _mouse_click_select_all(cart_page)
                if coord:
                    # 确保全选被勾上
                    if not coord.get('checked'):
                        cart_page.mouse.click(coord['x'], coord['y'])
                        cart_page.wait_for_timeout(2000)

                    # 读取金额判断是否有商品
                    amount = _read_bottom_bar_amount(cart_page)
                    if amount > 0:
                        logger.info(f"采购车有残留商品（¥{amount:.2f}），正在清空...")

                        # 用真实鼠标点击删除按钮
                        del_coord = cart_page.evaluate("""() => {
                            var all = document.querySelectorAll('button, a, div, span');
                            for (var i = 0; i < all.length; i++) {
                                var txt = String(all[i].innerText || '').trim();
                                if (txt === '删除') {
                                    var r = all[i].getBoundingClientRect();
                                    if (r.width > 20 && r.height > 15) {
                                        return {x: r.x + r.width/2, y: r.y + r.height/2};
                                    }
                                }
                            }
                            return null;
                        }""")
                        if del_coord:
                            cart_page.mouse.click(del_coord['x'], del_coord['y'])
                            cart_page.wait_for_timeout(2000)
                            # 确认弹窗（可能弹出"确认删除"）
                            _confirm_popup(cart_page)
                            cart_page.wait_for_timeout(2000)
                            logger.info("已清空采购车残留商品")
                        else:
                            logger.warning("未找到删除按钮")
                    else:
                        logger.info("采购车为空，无需清理")
                else:
                    logger.warning("未找到全选按钮")
        except Exception as e:
            logger.warning(f"清空采购车失败: {e}")
        finally:
            if cart_page:
                try:
                    cart_page.close()
                except Exception:
                    pass

    def _do_searching(self):
        """以图搜图。"""
        config = self.task_data["config"]
        context = self.runtime["context"]
        page = self.runtime["page"]

        from src.search import image_search
        search_image = config.get("search", {}).get("image_path", "")
        if not search_image:
            raise FatalError("未配置搜索图片")
        self.runtime["result_page"] = image_search(context, page, search_image)

    def _do_entering_shop(self):
        """定位目标店铺。"""
        config = self.task_data["config"]
        context = self.runtime["context"]

        from src.shop import find_shop_and_enter
        shop_name = config.get("search", {}).get("target_shop_name", "")
        if not shop_name:
            raise FatalError("未配置目标店铺名称")
        self.runtime["shop_page"] = find_shop_and_enter(context, self.runtime["result_page"], shop_name)

    def _do_filling_cart(self):
        """加购商品。"""
        config = self.task_data["config"]
        context = self.runtime["context"]

        from src.cart import run_cart_filling
        cart_cfg = config.get("cart", {})
        cart_cfg["_shop_name"] = config.get("search", {}).get("target_shop_name", "")

        self.task_data["added"] = run_cart_filling(
            context, self.runtime["shop_page"], cart_cfg,
            progress_callback=self._send_progress,
            cancel_check=self._is_cancelled,
        )

    def _do_cart_filled(self):
        """加购完成。"""
        pass  # 状态转换由引擎自动处理

    def _do_awaiting_approval(self):
        """等待结算审批。"""
        self._set_waiting("等待管理面板确认结算")
        self._approve_event.wait()

        if self._is_cancelled():
            return False
        if not self._approved:
            self._transition(STATUS_COMPLETED, "用户选择不结算")
            return False

    def _do_checking_out(self):
        """结算下单。"""
        config = self.task_data["config"]
        context = self.runtime["context"]

        from src.cart import run_cart_checkout
        order_limit = config.get("cart", {}).get("order_limit", 500)
        shipping_reserve = config.get("cart", {}).get("shipping_reserve", 15)
        shop_name = config.get("search", {}).get("target_shop_name", "")

        orders, actual_amount = run_cart_checkout(
            context, order_limit=order_limit,
            shipping_reserve=shipping_reserve, shop_name=shop_name
        )
        self.task_data["orders"] = orders
        self.task_data["actual_amount"] = actual_amount
