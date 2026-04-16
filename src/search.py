import logging
import os
import time
from src.utils import save_screenshot, random_delay

logger = logging.getLogger("1688-auto")

# 搜图入口按钮选择器（相机图标）
CAMERA_BTN_SELECTORS = [
    # 当前版本（2025）
    ".image-input-button",
    ".image-upload-button-camera",
    ".image-upload-button-container",
    # 旧版兜底
    ".search-img-btn",
    "[class*='imgSearch']",
    ".img-search-btn",
    "button[data-spm*='img']",
    ".search-bar-camera",
    "[title*='图片搜索']",
    "[title*='拍照搜索']",
    "[aria-label*='图片搜索']",
]

# 图片上传 input 选择器
UPLOAD_INPUT_SELECTORS = [
    # 当前版本（2025）
    "input.image-file-reader-wrapper",
    ".image-file-reader-wrapper",
    ".search-image-upload-container input",
    ".image-upload-button-container input",
    # 通用兜底
    "input[type='file'][accept*='image']",
    "input[type='file']",
    "[class*='imgSearch'] input",
    "[class*='imageSearch'] input",
]

# 搜索图片按钮（上传后点击触发搜索）
# 注意：不用 div:has-text，避免命中大容器导致点击位置偏离
SEARCH_BTN_SELECTORS = [
    # 当前版本（2025）：实际渲染为 div.search-btn
    "div.search-btn",
    # 精确匹配按钮/span/a
    "button:has-text('搜索图片')",
    "span:has-text('搜索图片')",
    "a:has-text('搜索图片')",
    # 通用兜底
    ".img-search-confirm",
    "button[class*='confirm']",
    ".search-img-confirm",
    "[class*='imgSearch'] button",
    "[class*='imageSearch'] button",
    "button:has-text('搜索')",
    "button:has-text('确定')",
]


def _dump_search_bar_debug(page):
    """找不到搜图入口时，把搜索栏区域的 HTML 打印出来供分析"""
    try:
        logger.error(f"[DEBUG] 当前 URL: {page.url}")
        # 搜索栏容器
        html_chunks = page.eval_on_selector_all(
            "form, [class*='search'], [class*='Search'], header",
            "els => els.slice(0, 5).map(e => '<<' + e.className + '>>\\n' + e.outerHTML.slice(0, 800))"
        )
        logger.error("[DEBUG] 搜索区域 HTML（前5个元素，截断800字符）:")
        for chunk in html_chunks:
            logger.error(chunk)
        # 所有包含 camera / image / img / pic 的元素
        candidates = page.eval_on_selector_all(
            "[class*='camera'],[class*='Camera'],[class*='image'],[class*='Image'],"
            "[class*='img'],[class*='Img'],[class*='pic'],[class*='Pic']",
            "els => els.slice(0, 20).map(e => e.tagName + ' class=' + e.className + ' title=' + (e.title||'') + ' aria=' + (e.getAttribute('aria-label')||''))"
        )
        logger.error("[DEBUG] 含 camera/image/img/pic 的元素（前20个）:")
        for c in candidates:
            logger.error(f"  {c}")
    except Exception as e:
        logger.error(f"[DEBUG] 调试信息收集失败: {e}")


def _dump_after_upload_debug(page):
    """上传后找不到搜索按钮时，递归穿透 Shadow DOM 和 iframe 输出调试信息"""
    try:
        logger.error(f"[DEBUG] 当前 URL: {page.url}")

        # 1. 列出所有 iframe
        iframes = page.eval_on_selector_all(
            "iframe",
            "els => els.map(e => 'src=' + e.src + ' id=' + e.id + ' class=' + e.className)"
        )
        logger.error(f"[DEBUG] 页面 iframe 列表({len(iframes)}个):")
        for f in iframes:
            logger.error(f"  {f}")

        # 2. 递归搜索所有 Shadow DOM，找含"搜索"文字的元素
        result = page.evaluate("""() => {
            const found = [];
            function walk(root) {
                const all = root.querySelectorAll('*');
                for (const el of all) {
                    const txt = (el.innerText || el.textContent || '').trim();
                    if (txt && txt.length < 20 && txt.includes('搜索')) {
                        const r = el.getBoundingClientRect();
                        found.push(el.tagName + ' | class=' + el.className +
                                   ' | text=' + txt +
                                   ' | visible=' + (r.width > 0 && r.height > 0));
                    }
                    if (el.shadowRoot) walk(el.shadowRoot);
                }
            }
            walk(document);
            return found.slice(0, 30);
        }""")
        logger.error('[DEBUG] Shadow DOM 穿透搜索 - 含"搜索"文字的元素:')
        for r in result:
            logger.error(f"  {r}")

        # 3. 输出整个 body 的浅层 HTML（找到上传后出现的新容器）
        body_html = page.evaluate(
            "() => document.body.innerHTML.slice(0, 3000)"
        )
        logger.error(f"[DEBUG] body innerHTML 前3000字符:\n{body_html}")

    except Exception as ex:
        logger.error(f"[DEBUG] 调试信息收集失败: {ex}")


def _try_upload_direct(page, image_path: str) -> bool:
    """尝试直接向 file input 写入文件（input 可能是隐藏元素，无需先点按钮）"""
    for sel in UPLOAD_INPUT_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el:
                el.set_input_files(image_path)
                logger.info(f"图片上传成功（直接写入）: {sel}")
                return True
        except Exception as e:
            logger.debug(f"上传尝试失败 {sel}: {e}")
            continue
    return False


def _find_and_click(page, selectors: list, label: str):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                logger.info(f"点击 {label}: {sel}")
                return True
        except Exception:
            continue
    return False


def image_search(context, page, image_path: str):
    """
    执行搜图流程：
    1. 点击搜图入口（相机图标）
    2. 上传图片
    3. 点击搜索按钮
    4. 捕获新标签页并返回
    """
    from src.login import wait_for_verification

    image_path = os.path.expanduser(image_path)
    # 相对路径基于项目根目录解析
    if not os.path.isabs(image_path):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        image_path = os.path.join(project_root, image_path)
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"图片文件不存在: {image_path}")

    logger.info(f"开始搜图，图片路径: {image_path}")

    # 确保在首页
    if "1688.com" not in page.url:
        page.goto("https://www.1688.com", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

    # 搜图前检测滑块验证（手动退出登录后可能触发风控）
    wait_for_verification(page)

    # 策略一：直接向隐藏的 file input 上传（当前版本无需点相机按钮）
    uploaded = _try_upload_direct(page, image_path)

    # 策略二：先点相机按钮展开面板，再上传
    if not uploaded:
        if not _find_and_click(page, CAMERA_BTN_SELECTORS, "搜图入口"):
            _dump_search_bar_debug(page)
            save_screenshot(page, "camera_btn_not_found")
            raise RuntimeError("未找到搜图入口按钮，请检查日志中的 [DEBUG] 信息确认实际 class 名称")
        random_delay(1.0, 2.0)
        uploaded = _try_upload_direct(page, image_path)

    if not uploaded:
        save_screenshot(page, "upload_input_not_found")
        raise RuntimeError("未找到图片上传输入框，请检查页面结构")

    # 等待"搜索图片"按钮渲染出来（最多 8 秒）
    search_btn = None
    search_btn_sel = None
    deadline = time.time() + 8
    while time.time() < deadline and search_btn is None:
        for sel in SEARCH_BTN_SELECTORS:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    search_btn = el
                    search_btn_sel = sel
                    break
            except Exception:
                continue
        if search_btn is None:
            page.wait_for_timeout(500)

    if search_btn is None:
        _dump_after_upload_debug(page)
        save_screenshot(page, "search_btn_not_found")
        raise RuntimeError('未找到"搜索图片"按钮，请检查日志 [DEBUG] 信息及截图')

    logger.info(f"找到搜索按钮: {search_btn_sel}")

    url_before = page.url

    logger.info("点击搜索图片按钮...")
    try:
        # 先尝试监听新标签页（超时 6 秒）
        with context.expect_page(timeout=6000) as new_page_info:
            # 用 JS click 确保点击落在元素本身而非父容器
            search_btn.dispatch_event("click")
        result_page = new_page_info.value
        result_page.wait_for_load_state("domcontentloaded")
        result_page.wait_for_timeout(2000)
        logger.info(f"搜索结果页（新标签）: {result_page.url}")
        return result_page
    except Exception:
        # 没有新标签页，等当前页 URL 变化
        logger.info("未检测到新标签页，等待当前页跳转...")
        try:
            page.wait_for_url(lambda url: url != url_before, timeout=10000)
        except Exception:
            pass
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2000)
        logger.info(f"搜索结果页（当前页）: {page.url}")
        return page
