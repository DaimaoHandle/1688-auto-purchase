import logging
import time
from src.utils import save_screenshot
from src.selector_health import try_selectors

logger = logging.getLogger("1688-auto")

# ─── 滑块 / 验证码页面检测 ────────────────────────────────────────────────────

# URL 中包含以下关键字则认为是验证页面
_VERIFY_URL_KEYWORDS = [
    "identity.1688.com",
    "login.taobao.com",
    "passport.1688.com",
    "checkcode",
    "slidecode",
    "punish",
    "risk.taobao",
    "sec.taobao",
    "verify",
    "captcha",
    "baxia",
]

# 页面中包含以下元素则认为是验证页面
_VERIFY_SELECTORS = [
    ".nc_wrapper",
    "#nc_1_wrapper",
    "[id^='nc_']",
    "[class*='nc-lang']",
    "[class*='slide-captcha']",
    "[class*='slideCaptcha']",
    ".btn_slide",
    "[class*='verifyWrap']",
    "[class*='checkcode']",
    "[class*='captcha']",
    "[class*='baxia']",
    "[class*='punish']",
    # 1688 新版滑块
    "[class*='slider']",
    "[class*='Slider']",
    "#baxia-dialog-content",
    "[id*='baxia']",
    "[class*='verify-wrapper']",
    "[class*='smc-modal']",
    "iframe[src*='captcha']",
    "iframe[src*='punish']",
    "iframe[src*='verify']",
    "iframe[src*='baxia']",
]


def is_verification_page(page) -> bool:
    """检测当前页面是否为滑块验证 / 风控验证码页面。"""
    try:
        # 1. URL 关键词检测
        url = page.url.lower()
        for kw in _VERIFY_URL_KEYWORDS:
            if kw in url:
                return True

        # 2. DOM 元素检测（选择器）
        el = try_selectors(page, _VERIFY_SELECTORS, "验证码检测", check_visible=True)
        if el:
            return True

        # 3. JS 深度检测：检查页面文字和 iframe
        has_verify = page.evaluate("""() => {
            // 检查页面是否有验证相关文字
            var bodyText = document.body ? (document.body.innerText || '') : '';
            if (bodyText.indexOf('滑块验证') !== -1 || bodyText.indexOf('请完成验证') !== -1
                || bodyText.indexOf('拖动滑块') !== -1 || bodyText.indexOf('安全验证') !== -1
                || bodyText.indexOf('人机验证') !== -1) {
                return true;
            }
            // 检查 iframe 中是否有验证页面
            var iframes = document.querySelectorAll('iframe');
            for (var i = 0; i < iframes.length; i++) {
                var src = (iframes[i].src || '').toLowerCase();
                if (src.indexOf('captcha') !== -1 || src.indexOf('verify') !== -1
                    || src.indexOf('baxia') !== -1 || src.indexOf('punish') !== -1
                    || src.indexOf('slide') !== -1) {
                    return true;
                }
            }
            return false;
        }""")
        if has_verify:
            return True
    except Exception:
        pass
    return False


def wait_for_verification(page, timeout_ms: int = 120000):
    """
    若当前页面为滑块 / 验证码页面，等待用户手动完成验证后继续。
    验证通过（页面离开验证页）后自动返回。
    """
    if not is_verification_page(page):
        return

    logger.info("=" * 50)
    logger.info("检测到滑块 / 验证码页面，请手动完成验证")
    logger.info(f"等待时间最长 {timeout_ms // 1000} 秒")
    logger.info("=" * 50)
    print("\n>>> 检测到安全验证，请在浏览器中完成滑块验证，完成后程序自动继续 <<<\n")

    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        page.wait_for_timeout(1500)
        if not is_verification_page(page):
            logger.info("验证已完成，继续执行")
            page.wait_for_timeout(1000)  # 等待页面跳转稳定
            return

    save_screenshot(page, "verification_timeout")
    raise TimeoutError("等待滑块验证超时，请重新运行程序")


# ─── 登录成功/失败 选择器 ─────────────────────────────────────────────────────

# 登录成功后页面上会出现的元素（只要命中一个即视为已登录）
LOGIN_PRESENT_SELECTORS = [
    ".member-nick",
    ".header-login-info",
    "[class*='userNick']",
    "[class*='member-nick']",
    "[class*='UserNick']",
    ".site-nav-user",
    "[class*='login-info']",
    "[class*='loginInfo']",
    "[class*='user-info']",
    "[class*='userInfo']",
    "[class*='header-user']",
    "[class*='headerUser']",
    "[class*='nav-user']",
    "[class*='navUser']",
    # 1688 买家中心链接
    "a[href*='buyercenter']",
    "a[href*='trade.1688.com']",
    "a[href*='i.1688.com']",
]

# 未登录时才存在的元素（出现则说明未登录）
NOT_LOGGED_IN_SELECTORS = [
    "a[href*='login.1688.com']",
    "a[href*='passport.1688.com']",
    "[class*='login-btn']",
    "[class*='loginBtn']",
    "button[class*='login']",
]


def _check_login_cookie(page) -> bool:
    """通过 cookie 判断：1688 登录后会带有 _tb_token_ 或 cookie2"""
    try:
        cookies = page.context.cookies()
        login_cookie_names = {"_tb_token_", "cookie2", "t", "unb", "uc3", "lgc"}
        found = {c["name"] for c in cookies if c["name"] in login_cookie_names}
        if len(found) >= 2:
            logger.debug(f"登录 cookie 检测命中: {found}")
            return True
    except Exception as e:
        logger.debug(f"cookie 检测异常: {e}")
    return False


def _check_not_on_login_page(page) -> bool:
    """当前 URL 不包含 login / passport 则可能已登录"""
    try:
        url = page.url.lower()
        return "login" not in url and "passport" not in url and "signin" not in url
    except Exception:
        return False


def is_logged_in(page) -> bool:
    # 1. 未登录元素存在 → 明确未登录
    el = try_selectors(page, NOT_LOGGED_IN_SELECTORS, "未登录标志", check_visible=True)
    if el:
        return False

    # 2. 登录成功元素存在 → 已登录
    el = try_selectors(page, LOGIN_PRESENT_SELECTORS, "已登录标志", check_visible=True)
    if el:
        return True

    # 3. cookie 判断
    if _check_login_cookie(page):
        return True

    return False


def _dump_login_debug(page):
    """登录超时时输出调试信息，帮助更新选择器"""
    try:
        url = page.url
        title = page.title()
        logger.error(f"[DEBUG] 当前 URL: {url}")
        logger.error(f"[DEBUG] 当前标题: {title}")

        # 打印页面中所有 a 标签的 href（找登录相关链接）
        links = page.eval_on_selector_all(
            "a[href]",
            "els => els.slice(0, 30).map(e => e.href + ' | ' + e.innerText.trim().slice(0, 20))"
        )
        logger.error("[DEBUG] 页面链接（前30条）:")
        for lnk in links:
            logger.error(f"  {lnk}")

        # 打印顶部导航区域的 HTML（最可能含登录信息）
        nav_html = page.eval_on_selector_all(
            "header, nav, [class*='header'], [class*='nav'], [class*='top-bar']",
            "els => els.slice(0, 3).map(e => e.outerHTML.slice(0, 500))"
        )
        logger.error("[DEBUG] 导航区域 HTML（前3个，截断500字符）:")
        for h in nav_html:
            logger.error(f"  {h}")
    except Exception as e:
        logger.error(f"[DEBUG] 调试信息收集失败: {e}")


def wait_for_login(page, timeout_ms: int = 120000):
    """打开1688首页，检测登录状态，未登录则等待用户手动登录。
    登录过程中若出现滑块 / 风控验证页面，自动暂停并等待用户手动完成验证。
    """
    logger.info("正在打开 1688.com ...")
    page.goto("https://www.1688.com", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    # 打开后立即检测是否有验证码
    wait_for_verification(page, timeout_ms)

    if is_logged_in(page):
        logger.info("检测到已登录状态，继续执行")
        return

    logger.info("=" * 50)
    logger.info("请在浏览器中手动登录 1688 账号")
    logger.info(f"等待时间最长 {timeout_ms // 1000} 秒")
    logger.info("=" * 50)
    print("\n>>> 请在浏览器窗口中完成登录，登录成功后程序将自动继续 <<<\n")

    deadline = time.time() + timeout_ms / 1000
    check_count = 0
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        check_count += 1

        # 轮询期间若出现验证码页面，暂停等待
        remaining_ms = int((deadline - time.time()) * 1000)
        if remaining_ms > 0:
            wait_for_verification(page, remaining_ms)

        if is_logged_in(page):
            logger.info(f"登录成功（第 {check_count} 次检测命中），继续执行")
            return

        if check_count % 10 == 0:
            try:
                logger.info(f"等待登录中... 当前页面: {page.url}")
            except Exception:
                pass

    _dump_login_debug(page)
    save_screenshot(page, "login_timeout")
    raise TimeoutError("等待登录超时，请重新运行程序")
