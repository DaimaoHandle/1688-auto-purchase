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
    STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED, STATUS_PAUSED,
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

class TaskCancelled(Exception):
    """任务被取消（暂停期间取消或正常取消）"""
    pass


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
        self._pause_event = threading.Event()
        self._pause_event.set()  # 初始为非暂停状态
        self._paused = False
        self._pre_pause_status = None  # 暂停前的状态，恢复时还原
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
        self._pause_event.set()
        self._paused = False
        self._pre_pause_status = None
        self._approve_event.clear()
        self._approved = False
        self._running = True

        # 注入 checkpoint 到 src 模块，使其能在操作间隙暂停
        from src.cart import set_checkpoint as cart_set_cp
        from src.shop import set_checkpoint as shop_set_cp
        cart_set_cp(self.checkpoint)
        shop_set_cp(self.checkpoint)

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
        self._pause_event.set()  # 解除暂停阻塞，让线程能响应取消
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

    def pause_task(self):
        """暂停任务：清除 pause_event，工作线程将在下一个 checkpoint 处阻塞。"""
        if not self._running or self._paused:
            return
        self._paused = True
        self._pre_pause_status = self.task_data.get("state", "")
        self._pause_event.clear()
        self._send_status(STATUS_PAUSED, "用户暂停")
        logger.info("[暂停] 任务已暂停，将在下一个检查点停止")

    def resume_task(self):
        """恢复任务：设置 pause_event，工作线程从 checkpoint 处继续。"""
        if not self._running or not self._paused:
            return
        self._paused = False
        self._pause_event.set()
        # 恢复暂停前的状态
        if self._pre_pause_status:
            self._send_status(self._pre_pause_status, "已恢复")
        logger.info("[恢复] 任务已恢复执行")

    def checkpoint(self):
        """
        细粒度检查点 — 在每个 Playwright 操作之间调用。
        如果暂停中则阻塞，直到恢复或取消。
        如果已取消则抛出异常。
        """
        if self._is_cancelled():
            raise TaskCancelled("任务已取消")
        if not self._pause_event.is_set():
            logger.info("[暂停] 到达检查点，等待恢复...")
            # 阻塞等待恢复或取消
            while not self._pause_event.is_set():
                if self._is_cancelled():
                    raise TaskCancelled("任务已取消")
                self._pause_event.wait(timeout=1.0)
            logger.info("[恢复] 从检查点继续执行")

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
                except TaskCancelled:
                    self._transition(STATUS_CANCELLED, "用户取消")
                    return

                # handler 返回 False 表示流程终止（如用户拒绝）
                if result is False:
                    return

            # 全部完成
            td = self.task_data
            self._transition(STATUS_COMPLETED,
                f"完成！{td['added']} 件商品，{td['orders']} 笔订单，¥{td['actual_amount']:.2f}")

        except TaskCancelled:
            if self.task_data.get("state") != STATUS_CANCELLED:
                self._transition(STATUS_CANCELLED, "用户取消")

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
        self._paused = False
        self._pause_event.set()

        # 清除 src 模块的 checkpoint 引用
        try:
            from src.cart import set_checkpoint as cart_set_cp
            from src.shop import set_checkpoint as shop_set_cp
            cart_set_cp(None)
            shop_set_cp(None)
        except Exception:
            pass

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
        """打开1688首页，等待用户手动登录。"""
        self._set_waiting("等待用户在浏览器中登录 1688")
        config = self.task_data["config"]
        page = self.runtime["page"]

        # 先打开 1688 首页
        logger.info("正在打开 1688.com ...")
        page.goto("https://www.1688.com", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        from src.login import is_logged_in, is_verification_page, wait_for_verification

        # 检测是否有验证码
        if is_verification_page(page):
            logger.warning("检测到验证码，等待手动处理...")
            wait_for_verification(page)

        if is_logged_in(page):
            logger.info("检测到已登录状态")
            return

        logger.info("请在浏览器中手动登录 1688 账号")
        timeout_ms = config.get("timeouts", {}).get("login_wait", 120000)
        deadline = time.time() + timeout_ms / 1000

        while not is_logged_in(page):
            if self._is_cancelled():
                return False
            if time.time() > deadline:
                raise ManualError("登录等待超时，请检查浏览器")
            # 检测验证码
            if is_verification_page(page):
                wait_for_verification(page)
            page.wait_for_timeout(2000)

    def _do_logged_in(self):
        """获取采购车 URL，检查并清空残留商品。"""
        page = self.runtime["page"]
        context = self.runtime["context"]
        from src.cart import capture_cart_url, set_cart_url, _open_cart_in_new_tab, _mouse_click_select_all, _read_bottom_bar_amount, _confirm_popup

        cart_url = capture_cart_url(page)
        if cart_url:
            set_cart_url(cart_url)

        # 检查采购车是否有残留商品，如果有则清空
        logger.info("检查采购车是否有残留商品...")
        cart_page = None
        try:
            cart_page = _open_cart_in_new_tab(context)
            if cart_page:
                # 等待页面完全加载
                cart_page.wait_for_timeout(5000)

                # 检查页面上是否有商品（通过 TBODY 数量判断，比读取金额更可靠）
                has_items = cart_page.evaluate("""() => {
                    var tbodies = document.querySelectorAll('tbody');
                    var count = 0;
                    for (var i = 0; i < tbodies.length; i++) {
                        var r = tbodies[i].getBoundingClientRect();
                        if (r.width > 400 && r.height > 50) count++;
                    }
                    return count;
                }""")

                if has_items and has_items > 0:
                    logger.info(f"采购车有 {has_items} 个残留商品，正在清空...")

                    # 找底部栏的全选 checkbox 并用鼠标点击
                    for attempt in range(3):
                        coord = _mouse_click_select_all(cart_page)
                        if coord:
                            cart_page.mouse.click(coord['x'], coord['y'])
                            cart_page.wait_for_timeout(3000)
                            break
                        cart_page.wait_for_timeout(2000)

                    # 找删除按钮并用鼠标点击（按钮文字可能是"删除"或"删除 N"）
                    for attempt in range(3):
                        del_coord = cart_page.evaluate("""() => {
                            var all = document.querySelectorAll('button');
                            for (var i = 0; i < all.length; i++) {
                                var txt = String(all[i].innerText || '').trim();
                                if (txt.indexOf('删除') !== -1 && txt.length < 10) {
                                    var r = all[i].getBoundingClientRect();
                                    if (r.width > 15 && r.height > 10) {
                                        return {x: r.x + r.width/2, y: r.y + r.height/2, txt: txt};
                                    }
                                }
                            }
                            return null;
                        }""")
                        if del_coord:
                            cart_page.mouse.click(del_coord['x'], del_coord['y'])
                            cart_page.wait_for_timeout(3000)

                            # 确认弹窗（按钮可能是"删除"、"确定"、"确认"）
                            confirm_coord = cart_page.evaluate("""() => {
                                var keywords = ['删除', '确定', '确认'];
                                var all = document.querySelectorAll('button');
                                for (var k = 0; k < keywords.length; k++) {
                                    for (var i = 0; i < all.length; i++) {
                                        var txt = String(all[i].innerText || '').trim();
                                        if (txt === keywords[k]) {
                                            var r = all[i].getBoundingClientRect();
                                            if (r.width > 20 && r.height > 15) {
                                                return {x: r.x + r.width/2, y: r.y + r.height/2};
                                            }
                                        }
                                    }
                                }
                                return null;
                            }""")
                            if confirm_coord:
                                cart_page.mouse.click(confirm_coord['x'], confirm_coord['y'])
                                cart_page.wait_for_timeout(3000)

                            logger.info("已清空采购车残留商品")
                            break
                        else:
                            logger.warning(f"第{attempt+1}次未找到删除按钮，重试...")
                            cart_page.wait_for_timeout(2000)
                else:
                    logger.info("采购车为空，无需清理")
        except Exception as e:
            logger.warning(f"清空采购车失败: {e}")
        finally:
            if cart_page:
                try:
                    cart_page.close()
                except Exception:
                    pass

    def _do_searching(self):
        """搜索商品：搜图模式或搜店模式。"""
        config = self.task_data["config"]
        context = self.runtime["context"]
        page = self.runtime["page"]
        search_mode = config.get("search", {}).get("search_mode", "image")

        if search_mode == "shop":
            # 搜店模式：在搜索框输入店铺名搜索
            from src.search import shop_search
            shop_name = config.get("search", {}).get("target_shop_name", "")
            if not shop_name:
                raise FatalError("未配置目标店铺名称")
            logger.info(f"搜店模式：搜索 [{shop_name}]")
            self.runtime["result_page"] = shop_search(context, page, shop_name)
        else:
            # 搜图模式：以图搜图
            from src.search import image_search
            search_image = config.get("search", {}).get("image_path", "")
            if not search_image:
                raise FatalError("未配置搜索图片")
            logger.info("搜图模式：以图搜图")
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
            pause_check=self.checkpoint,
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
