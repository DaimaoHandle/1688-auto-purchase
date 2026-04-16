"""
错误重试与自愈模块。

提供重试装饰器、熔断器、页面健康检测。
"""
import time
import random
import logging
import functools

logger = logging.getLogger("1688-auto")


class CircuitBreaker:
    """
    熔断器：追踪连续失败次数，触发不同级别的恢复策略。

    级别：
    0 - 正常（连续失败 < threshold）：重试当前操作
    1 - 警告（连续失败 >= threshold）：刷新页面后重试
    2 - 严重（连续失败 >= threshold * 2）：需要导航回已知页面
    3 - 危险（连续失败 >= threshold * 3）：需要重启浏览器
    4 - 中止（连续失败 >= threshold * 4）：放弃当前任务
    """

    def __init__(self, threshold: int = 5):
        self.threshold = threshold
        self._consecutive_failures = 0
        self._total_failures = 0

    def record_success(self):
        """记录一次成功，重置连续失败计数。"""
        self._consecutive_failures = 0

    def record_failure(self):
        """记录一次失败。"""
        self._consecutive_failures += 1
        self._total_failures += 1

    @property
    def level(self) -> int:
        """当前熔断级别（0-4）。"""
        if self._consecutive_failures < self.threshold:
            return 0
        elif self._consecutive_failures < self.threshold * 2:
            return 1
        elif self._consecutive_failures < self.threshold * 3:
            return 2
        elif self._consecutive_failures < self.threshold * 4:
            return 3
        else:
            return 4

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def total_failures(self) -> int:
        return self._total_failures

    def should_abort(self) -> bool:
        return self.level >= 4


def with_retry(max_attempts: int = 3, backoff_base: float = 2.0, backoff_max: float = 30.0,
               on_retry_msg: str = ""):
    """
    重试装饰器。操作失败时自动重试，指数退避 + 随机抖动。

    Args:
        max_attempts: 最大尝试次数（含首次）
        backoff_base: 退避基数（秒）
        backoff_max: 最大退避时间（秒）
        on_retry_msg: 重试时的日志前缀
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_attempts:
                        delay = min(backoff_base * (2 ** (attempt - 1)), backoff_max)
                        delay *= (0.5 + random.random())  # 随机抖动
                        msg = on_retry_msg or func.__name__
                        logger.warning(f"[重试] {msg} 第{attempt}次失败: {e}，{delay:.1f}秒后重试...")
                        time.sleep(delay)
                    else:
                        msg = on_retry_msg or func.__name__
                        logger.error(f"[重试] {msg} 全部 {max_attempts} 次尝试均失败: {e}")
            raise last_error
        return wrapper
    return decorator


def is_page_alive(page) -> bool:
    """检测页面是否存活（能否执行 JS）。"""
    try:
        page.evaluate("() => true", timeout=5000)
        return True
    except Exception:
        return False


def check_for_verification(page) -> bool:
    """
    检测当前页面是否弹出了验证码/滑块。
    如果检测到，记录日志并返回 True。
    """
    try:
        is_verify = page.evaluate("""() => {
            var url = location.href.toLowerCase();
            if (url.indexOf('identity') !== -1 || url.indexOf('verify') !== -1
                || url.indexOf('captcha') !== -1 || url.indexOf('risk') !== -1) {
                return true;
            }
            // 检查页面中是否有滑块验证元素
            var sliders = document.querySelectorAll(
                '[class*="slider"], [class*="captcha"], [class*="verify"], [id*="captcha"], [id*="slider"]'
            );
            for (var i = 0; i < sliders.length; i++) {
                var r = sliders[i].getBoundingClientRect();
                if (r.width > 50 && r.height > 20) return true;
            }
            return false;
        }""")
        if is_verify:
            logger.warning("[验证码] 检测到验证码/滑块，请手动完成验证")
        return is_verify
    except Exception:
        return False


def wait_for_verification_clear(page, timeout_s: int = 120):
    """等待验证码被手动完成，最长等待 timeout_s 秒。"""
    logger.info(f"等待手动完成验证码（最长 {timeout_s} 秒）...")
    print(f"\n  >>> 检测到验证码，请在浏览器中手动完成验证 <<<\n")
    start = time.time()
    while time.time() - start < timeout_s:
        if not check_for_verification(page):
            logger.info("验证码已完成")
            return True
        time.sleep(2)
    logger.warning(f"验证码等待超时 ({timeout_s}秒)")
    return False


def try_refresh_page(page) -> bool:
    """尝试刷新当前页面。"""
    try:
        url = page.url
        logger.info(f"[自愈] 刷新页面: {url}")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        return True
    except Exception as e:
        logger.warning(f"[自愈] 刷新失败: {e}")
        return False
