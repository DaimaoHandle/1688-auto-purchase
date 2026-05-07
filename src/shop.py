import logging
import re
from src.utils import save_screenshot, random_delay
from src.selector_health import try_selectors

logger = logging.getLogger("1688-auto")

# ─── 暂停检查点 ──────────────────────────────────────────
_checkpoint_fn = None

def set_checkpoint(fn):
    """由 worker.py 注入检查点函数，暂停时阻塞。"""
    global _checkpoint_fn
    _checkpoint_fn = fn

def _checkpoint():
    """细粒度暂停检查点，在每个 Playwright 操作之间调用。"""
    if _checkpoint_fn:
        _checkpoint_fn()

# 搜索结果中商品卡片的店铺名称选择器
SHOP_NAME_SELECTORS = [
    ".shop-name",
    ".company-name",
    "[class*='shopName']",
    "[class*='companyName']",
    ".offer-company-name",
    "a[class*='shop']",
]

# 商品卡片容器
ITEM_CARD_SELECTORS = [
    ".offer-list-row .offer-item",
    ".sm-offer-item",
    "[class*='offerItem']",
    ".list-item",
    ".img-search-result-item",
]

# 进入全店的链接
ENTER_SHOP_SELECTORS = [
    "a:has-text('进店逛逛')",
    "a:has-text('进入店铺')",
    "a:has-text('全部商品')",
    ".enter-shop",
    "[class*='enterShop']",
    ".shop-entry",
]

# 全店商品列表中的商品项
SHOP_ITEM_SELECTORS = [
    # 当前版本（2025）
    ".entry-card",
    ".newofferlist .entry-card",
    ".offerlist .entry-card",
    # 旧版兜底
    ".shop-offer-list .offer-item",
    ".sm-offer-item",
    "[class*='offerItem']",
    ".offer-list .item",
]

# 下一页按钮
NEXT_PAGE_SELECTORS = [
    "a.next-btn",
    ".pagination-next",
    "a:has-text('下一页')",
    "[class*='nextPage']",
    ".next-page",
]


def _match_shop_name(name_text: str, shop_name: str) -> bool:
    """
    双向部分匹配：
    - name_text 是页面显示的店铺名（可能被截断），shop_name 是配置的完整名称
    - 只要其中一个包含另一个即视为匹配
    """
    return shop_name in name_text or name_text in shop_name


def _dump_page_shop_names(page):
    """调试：用 JS 直接扫描页面所有可能的店铺名元素，输出 class 和文字"""
    try:
        results = page.evaluate("""() => {
            const found = [];
            // 遍历全部元素，找文字长度在 4-20 之间、包含"市/区/店/厂/行/有限"等特征的叶子节点
            const all = document.querySelectorAll('*');
            for (const el of all) {
                const children = el.children;
                if (children.length > 0) continue;  // 只看叶子节点
                const txt = (el.innerText || el.textContent || '').trim();
                if (txt.length < 4 || txt.length > 30) continue;
                if (/市|区|店|厂|行|有限|公司|贸易|电商|工厂|实业/.test(txt)) {
                    const r = el.getBoundingClientRect();
                    found.push({
                        tag: el.tagName,
                        cls: el.className,
                        txt: txt,
                        visible: r.width > 0 && r.height > 0
                    });
                }
            }
            return found.slice(0, 40);
        }""")
        logger.error(f"[DEBUG] 页面中疑似店铺名的元素 ({len(results)} 个):")
        for r in results:
            logger.error(f"  [{r['tag']}] class={r['cls']} | visible={r['visible']} | text={r['txt']}")
    except Exception as e:
        logger.error(f"[DEBUG] 店铺名调试失败: {e}")


def _scan_current_cards(page, shop_name: str):
    """
    扫描当前已渲染的所有商品卡片，返回匹配的卡片或 None。
    策略一：直接全页查找店铺名元素（不依赖卡片容器选择器），再向上找可点击祖先。
    策略二：兜底用卡片容器 + 店铺名选择器组合。
    """
    # 策略一：直接全页匹配店铺名元素，再向上找含商品详情链接的祖先，直接返回该链接
    SHOP_NAME_DIRECT_SELECTORS = [
        "[class*='shopName']",
        "[class*='shop-name']",
        "[class*='companyName']",
        "[class*='company-name']",
        ".shop-name",
        ".company-name",
        ".offer-company-name",
        ".desc-text",
    ]
    for sel in SHOP_NAME_DIRECT_SELECTORS:
        name_els = page.query_selector_all(sel)
        for name_el in name_els:
            try:
                # 只处理可见的元素
                try:
                    if not name_el.is_visible():
                        continue
                except Exception:
                    pass

                name_text = name_el.inner_text().strip()
                if not name_text or not _match_shop_name(name_text, shop_name):
                    continue
                logger.info(f"找到目标店铺商品（可见），显示名: 「{name_text}」")

                # 向上最多 12 层，找商品详情链接（排除相似搜索等非商品链接）
                product_link = page.evaluate_handle("""el => {
                    let node = el;
                    for (let i = 0; i < 12; i++) {
                        node = node.parentElement;
                        if (!node) break;
                        const a = node.querySelector(
                            'a[href*="detail.1688.com"], a[href*="/offer/"]'
                        );
                        if (a) {
                            const href = a.getAttribute('href') || '';
                            if (href.indexOf('similar') === -1 && href.indexOf('login') === -1
                                && href.indexOf('cart') === -1 && href.indexOf('javascript') === -1) {
                                return a;
                            }
                        }
                    }
                    return null;
                }""", name_el)
                link_el = product_link.as_element()
                if link_el:
                    href = link_el.get_attribute("href") or ""
                    logger.info(f"找到商品详情链接: {href[:80]}")
                    return link_el

                # 没有 detail 链接，返回店铺名元素本身（用于点击其附近的商品图片）
                logger.info("未找到详情链接，将点击店铺名附近区域")
                return name_el
            except Exception as ex:
                logger.error(f"[DEBUG] 策略一异常: {ex}")
                continue

    # 策略二：卡片容器 + 店铺名选择器组合（兜底）
    for card_sel in ITEM_CARD_SELECTORS:
        cards = page.query_selector_all(card_sel)
        if not cards:
            continue
        for card in cards:
            for name_sel in SHOP_NAME_SELECTORS:
                try:
                    name_el = card.query_selector(name_sel)
                    if name_el:
                        name_text = name_el.inner_text().strip()
                        if name_text and _match_shop_name(name_text, shop_name):
                            logger.info(f"找到目标店铺商品（策略二），显示名: 「{name_text}」")
                            return card
                except Exception:
                    continue
    return None


def _find_shop_item(page, shop_name: str, max_loads: int = 10):
    """
    在搜索结果页中找到目标店铺的第一个商品。
    逐步向下滚动（每次滚动一屏高度），触发懒加载，
    每次滚动后扫描新内容，最多加载 max_loads 次。
    """
    # 等待首屏商品加载
    page.wait_for_timeout(3000)

    # 先扫描首屏
    card = _scan_current_cards(page, shop_name)
    if card:
        return card

    loads_done = 0
    scroll_step = 1500  # 每次向下滚动 1500px（约一屏半）
    while loads_done < max_loads:
        prev_height = page.evaluate("() => document.body.scrollHeight")

        _checkpoint()  # 检查点：滚动加载前
        # 每次向下滚动固定距离
        page.evaluate(f"() => window.scrollBy(0, {scroll_step})")

        # 等待新内容加载（高度增加或到达底部）
        new_content_loaded = False
        for _ in range(10):
            page.wait_for_timeout(500)
            new_height = page.evaluate("() => document.body.scrollHeight")
            if new_height > prev_height:
                new_content_loaded = True
                break

        # 即使高度没变也扫描（可能当前视口内有新渲染的内容）
        loads_done += 1
        logger.info(f"滚动加载第 {loads_done}/{max_loads} 次，扫描店铺...")

        card = _scan_current_cards(page, shop_name)
        if card:
            return card

        # 检查是否已到底部
        at_bottom = page.evaluate("() => window.scrollY + window.innerHeight >= document.body.scrollHeight - 100")
        if not new_content_loaded and at_bottom:
            logger.info("已滚动到页面底部，停止加载")
            break

    logger.warning(f"滚动 {loads_done} 次后仍未找到店铺: {shop_name}")
    _dump_page_shop_names(page)
    return None


def _find_product_link(card):
    """
    从卡片元素中找到指向商品详情页的链接，跳过旺旺/咨询/店铺装修等功能性链接。
    优先级：href 含 detail.1688.com 或 offer > 其他 http 链接 > 第一个 <a>
    排除：href 含 ww/wangwang/chat/im/contact/trademode 的链接
    """
    EXCLUDE_PATTERNS = ["ww.", "wangwang", "/chat", "/im/", "contact", "trademode", "javascript"]
    all_links = card.query_selector_all("a")

    # 调试：打出卡片内所有链接
    logger.error(f"[DEBUG] 卡片内共 {len(all_links)} 个 <a> 链接:")
    for a in all_links:
        try:
            href = a.get_attribute("href") or ""
            cls = a.get_attribute("class") or ""
            txt = (a.inner_text() or "").strip()[:30]
            logger.error(f"  href={href[:80]} | class={cls} | text={txt}")
        except Exception:
            pass

    preferred = None
    fallback = None
    for a in all_links:
        try:
            href = (a.get_attribute("href") or "").lower()
            if not href:
                continue
            # 排除功能性链接
            if any(p in href for p in EXCLUDE_PATTERNS):
                continue
            # 优先：商品详情链接
            if "detail.1688.com" in href or "/offer/" in href:
                return a
            # 次优：任意 http 链接
            if href.startswith("http") and preferred is None:
                preferred = a
            # 兜底：第一个非排除链接
            if fallback is None:
                fallback = a
        except Exception:
            continue

    return preferred or fallback


def _send_message_to_service(context, detail_page):
    """
    在商品详情页点击客服按钮，发送消息，然后关闭客服标签页。
    """
    logger.info("准备给客服发消息...")

    # 找客服按钮并点击（通常包含"联系客服"、"客服"、"咨询"等文字，或是旺旺图标）
    try:
        coord = detail_page.evaluate("""() => {
            // 找包含客服/咨询/联系文字的可点击元素
            var keywords = ['联系客服', '客服', '咨询', '在线咨询', '立即咨询'];
            var all = document.querySelectorAll('a, button, div, span');
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                var txt = String(el.innerText || el.textContent || '').trim();
                for (var k = 0; k < keywords.length; k++) {
                    if (txt.indexOf(keywords[k]) !== -1 && txt.length < 15) {
                        var r = el.getBoundingClientRect();
                        if (r.width > 10 && r.height > 10 && r.top > 0 && r.top < window.innerHeight) {
                            return {x: r.x + r.width / 2, y: r.y + r.height / 2, txt: txt};
                        }
                    }
                }
            }
            // 兜底：找 href 含 ww 或 im 的链接（旺旺）
            var links = document.querySelectorAll('a[href*="ww."], a[href*="/im/"], a[href*="amos"]');
            for (var j = 0; j < links.length; j++) {
                var r2 = links[j].getBoundingClientRect();
                if (r2.width > 5 && r2.height > 5) {
                    return {x: r2.x + r2.width / 2, y: r2.y + r2.height / 2, txt: '旺旺'};
                }
            }
            return null;
        }""")

        if not coord:
            logger.warning("未找到客服按钮，跳过发消息")
            return

        logger.info(f"点击客服按钮: \"{coord['txt']}\" ({coord['x']:.0f},{coord['y']:.0f})")

        # 点击客服按钮
        detail_page.mouse.click(coord['x'], coord['y'])
        detail_page.wait_for_timeout(2000)

        # 检测是否弹出"客户端/网页版"选择提示框，如果弹出则选择网页版
        try:
            chose_web = detail_page.evaluate("""() => {
                var all = document.querySelectorAll('a, button, div, span');
                for (var i = 0; i < all.length; i++) {
                    var txt = String(all[i].innerText || '').trim();
                    if (txt.indexOf('网页版') !== -1 && txt.length < 20) {
                        var r = all[i].getBoundingClientRect();
                        if (r.width > 10 && r.height > 10) {
                            all[i].click();
                            return true;
                        }
                    }
                }
                return false;
            }""")
            if chose_web:
                logger.info("检测到客户端/网页版选择框，已选择网页版")
                detail_page.wait_for_timeout(2000)
        except Exception:
            pass

        # 捕获客服标签页：取除 detail_page 外最新打开的标签
        chat_page = None
        try:
            # 等待新标签出现（最多 8 秒）
            for _pw in range(4):
                pages = context.pages
                # 找不是 detail_page 的最新标签
                candidates = [p for p in pages if p != detail_page]
                if candidates:
                    chat_page = candidates[-1]  # 最后一个（最新打开的）
                    break
                detail_page.wait_for_timeout(2000)

            if not chat_page:
                logger.warning("未检测到新标签页，跳过发消息")
                return

            # 等待页面加载并稳定（客服页面可能有中间跳转）
            chat_page.wait_for_load_state("domcontentloaded")
            chat_page.wait_for_timeout(5000)
            logger.info(f"客服页面已打开: {chat_page.url}")

            # 如果页面还在跳转（URL 是 1688 首页等），再等一会
            for _rw in range(5):
                url = chat_page.url
                if 'im.' in url or 'ww.' in url or 'amos' in url or 'air.' in url or 'chat' in url:
                    break
                chat_page.wait_for_timeout(2000)
            logger.info(f"客服页面最终URL: {chat_page.url}")
        except Exception as e:
            logger.warning(f"捕获客服标签失败: {e}")
            if not chat_page:
                return

        # 客服聊天在 iframe 内，使用 Playwright frame 对象直接操作（自动处理坐标偏移）
        try:
            sent = False
            chat_frame = None

            # 等待 iframe 内输入框出现（最多 20 秒）
            for _wait in range(10):
                frames = chat_page.frames
                for f in frames:
                    try:
                        # 检查这个 frame 内是否有输入框
                        has_input = f.evaluate("""() => {
                            var el = document.querySelector('pre.edit[contenteditable="true"]');
                            if (el) return true;
                            var all = document.querySelectorAll('[contenteditable="true"]');
                            for (var i = 0; i < all.length; i++) {
                                var r = all[i].getBoundingClientRect();
                                if (r.width > 50 && r.height > 15) return true;
                            }
                            return false;
                        }""")
                        if has_input:
                            chat_frame = f
                            logger.info(f"找到客服输入框所在 frame: {f.url[:60]}")
                            break
                    except Exception:
                        continue
                if chat_frame:
                    break
                chat_page.wait_for_timeout(2000)

            if chat_frame:
                # 1. 点击输入框激活
                try:
                    chat_frame.click('pre.edit[contenteditable="true"]', timeout=3000)
                except Exception:
                    try:
                        chat_frame.click('[contenteditable="true"]', timeout=3000)
                    except Exception:
                        logger.warning("点击输入框失败")
                chat_page.wait_for_timeout(500)

                # 2. 全选清空（防止残留内容），再输入消息
                chat_page.keyboard.press("Control+a")
                chat_page.wait_for_timeout(200)
                chat_page.keyboard.press("Backspace")
                chat_page.wait_for_timeout(200)
                chat_page.keyboard.type("今天能发货吗", delay=50)
                chat_page.wait_for_timeout(500)
                logger.info("已输入消息文字")

                # 3. 按 Enter 发送（客服页面支持 Enter 发送）
                chat_page.keyboard.press("Enter")
                chat_page.wait_for_timeout(1500)
                sent = True
                logger.info("已发送消息: 今天能发货吗")
            else:
                logger.warning("未找到客服输入框")
        except Exception as e:
            logger.warning(f"发送消息失败: {e}")

        # 关闭客服标签页
        try:
            chat_page.close()
            logger.info("客服标签页已关闭")
        except Exception:
            pass

    except Exception as e:
        logger.warning(f"客服消息流程异常: {e}")


def find_shop_and_enter(context, result_page, shop_name: str):
    """
    在搜索结果中找到目标店铺商品，进入详情页，再进入全店商品列表
    返回全店商品列表页面对象
    """
    logger.info(f"在搜索结果中查找店铺: {shop_name}")

    card = _find_shop_item(result_page, shop_name)
    if not card:
        save_screenshot(result_page, "shop_not_found")
        raise RuntimeError(f"滚动加载多页后仍未找到店铺: {shop_name}，请查看截图确认页面内容")

    # 进入商品详情页（模拟真实点击，不直接跳转）
    _checkpoint()  # 检查点：点击商品进入详情页前
    logger.info("点击商品进入详情页...")
    with context.expect_page() as detail_page_info:
        try:
            href = card.get_attribute("href") if hasattr(card, "get_attribute") else None
            if href and ("detail.1688.com" in href or "/offer/" in href):
                # 直接是商品详情链接，用 JS 滚动到元素再点击
                logger.info(f"点击详情链接: {href[:80]}")
                try:
                    result_page.evaluate("(el) => el.scrollIntoView({block:'center'})", card)
                    result_page.wait_for_timeout(1000)
                except Exception:
                    pass
                card.click(timeout=5000)
            else:
                # 是店铺名元素，用 JS 找到附近的商品图片并点击
                try:
                    result_page.evaluate("(el) => el.scrollIntoView({block:'center'})", card)
                    result_page.wait_for_timeout(1000)
                except Exception:
                    pass

                # 用 JS 从店铺名元素往上找卡片容器中的商品图片
                img_coord = result_page.evaluate("""(el) => {
                    var node = el;
                    for (var i = 0; i < 10; i++) {
                        node = node.parentElement;
                        if (!node) break;
                        var img = node.querySelector('img');
                        if (img) {
                            var r = img.getBoundingClientRect();
                            if (r.width > 50 && r.height > 50) {
                                return {x: r.x + r.width/2, y: r.y + r.height/2};
                            }
                        }
                    }
                    // 兜底：用店铺名上方区域
                    var r2 = el.getBoundingClientRect();
                    if (r2.width > 0) return {x: r2.x + r2.width/2, y: r2.y - 80};
                    return null;
                }""", card)

                if img_coord:
                    logger.info(f"点击商品图片区域 ({img_coord['x']:.0f},{img_coord['y']:.0f})")
                    result_page.mouse.click(img_coord['x'], img_coord['y'])
                else:
                    raise RuntimeError("无法获取店铺名元素坐标")
        except Exception as e:
            # 检查是否因为弹出了验证码
            from src.login import is_verification_page, wait_for_verification
            if is_verification_page(result_page):
                logger.warning("检测到验证码，等待手动处理...")
                wait_for_verification(result_page)
                # 验证通过后重试一次
                raise RuntimeError(f"点击商品失败（已处理验证码，请重试）: {e}")
            raise RuntimeError(f"点击商品失败: {e}")

    detail_page = detail_page_info.value
    detail_page.wait_for_load_state("domcontentloaded")
    detail_page.wait_for_timeout(3000)
    logger.info(f"商品详情页: {detail_page.url}")

    # 给客服发消息
    _checkpoint()  # 检查点：发客服消息前
    _send_message_to_service(context, detail_page)

    # 在详情页找到进入全店的链接
    _checkpoint()  # 检查点：进入全店前
    logger.info("在详情页查找进入全店链接...")
    shop_page = _enter_shop_from_detail(context, detail_page)
    return shop_page


def _find_shop_goods_btn(detail_page):
    """
    在详情页店铺信息区找到「商品」按钮。
    页面结构：主图上方有店铺名 + 三个按钮（关注 / 客服 / 商品），
    「商品」按钮点击后会打开新标签页全店商品列表。
    用 JS 扫描所有可见叶子节点，找文字精确等于「商品」的元素。
    """
    result = detail_page.evaluate("""() => {
        const candidates = [];
        function walk(root) {
            for (const el of root.querySelectorAll('*')) {
                // 只看叶子或近叶子节点
                const txt = (el.innerText || '').trim();
                if (txt !== '商品') continue;
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    candidates.push({
                        tag: el.tagName,
                        cls: el.className,
                        x: r.x, y: r.y, w: r.width, h: r.height
                    });
                }
                if (el.shadowRoot) walk(el.shadowRoot);
            }
        }
        walk(document);
        return candidates;
    }""")

    if result:
        logger.info(f"找到 {len(result)} 个文字为「商品」的可见元素:")
        for r in result:
            logger.info(f"  [{r['tag']}] class={r['cls']} | x={r['x']:.0f}, y={r['y']:.0f}")

    # 返回第一个匹配的 Playwright 元素
    for info in result:
        cls = info["cls"]
        tag = info["tag"].lower()
        # 用 XPath 精确匹配文字
        try:
            el = detail_page.locator(f"xpath=//{tag}[normalize-space(text())='商品']").first
            if el and el.is_visible():
                return el
        except Exception:
            continue
    return None


def _enter_shop_from_detail(context, detail_page):
    """从商品详情页进入全店商品列表"""
    # 等待页面渲染完成
    detail_page.wait_for_timeout(2000)

    shop_page = None

    # 优先：找「商品」按钮（店铺信息区三按钮之一）
    btn = _find_shop_goods_btn(detail_page)
    if btn:
        logger.info("找到「商品」按钮，点击进入全店...")
        try:
            with context.expect_page() as shop_page_info:
                btn.click()
            shop_page = shop_page_info.value
            shop_page.wait_for_load_state("domcontentloaded")
            shop_page.wait_for_timeout(3000)
            logger.info(f"全店商品页（原始URL）: {shop_page.url}")
        except Exception as e:
            logger.warning(f"点击「商品」按钮失败: {e}")

    # 兜底：原有选择器列表
    if not shop_page:
        el = try_selectors(detail_page, ENTER_SHOP_SELECTORS, "进入店铺链接", check_visible=True)
        if el:
            try:
                logger.info("找到进店链接（兜底选择器）")
                with context.expect_page() as shop_page_info:
                    el.click()
                shop_page = shop_page_info.value
                shop_page.wait_for_load_state("domcontentloaded")
                shop_page.wait_for_timeout(3000)
                logger.info(f"全店商品页（原始URL）: {shop_page.url}")
            except Exception:
                pass

    if not shop_page:
        save_screenshot(detail_page, "enter_shop_failed")
        raise RuntimeError("无法进入全店商品列表，请查看截图确认「商品」按钮是否存在")

    logger.info(f"全店商品页: {shop_page.url}")
    return shop_page


def enter_new_product_zone(context, shop_page):
    """
    在全店商品页中点击"新品专区"标签。
    可能在当前页切换，也可能打开新标签页。
    返回新品专区的 page 对象（可能是 shop_page 本身或新标签页），失败返回 None。
    """
    try:
        # 记录当前标签数
        pages_before = set(id(p) for p in context.pages)

        result = shop_page.evaluate("""() => {
            var all = document.querySelectorAll('a, div, span, li, button');
            for (var i = 0; i < all.length; i++) {
                var txt = String(all[i].innerText || '').trim();
                if ((txt.indexOf('新品') !== -1 || txt.indexOf('上新') !== -1) && txt.length < 15) {
                    var r = all[i].getBoundingClientRect();
                    if (r.width > 10 && r.height > 10 && r.top > 0 && r.top < window.innerHeight) {
                        all[i].click();
                        return {clicked: true, txt: txt};
                    }
                }
            }
            return {clicked: false};
        }""")
        if not result or not result.get('clicked'):
            logger.warning("未找到新品专区入口")
            return None

        shop_page.wait_for_timeout(3000)
        logger.info(f"已点击新品专区: {result.get('txt')}")

        # 检查是否打开了新标签
        pages_after = context.pages
        new_pages = [p for p in pages_after if id(p) not in pages_before]
        if new_pages:
            new_page = new_pages[-1]
            new_page.wait_for_load_state("domcontentloaded")
            new_page.wait_for_timeout(3000)
            logger.info(f"新品专区在新标签打开: {new_page.url}")
            return new_page
        else:
            return shop_page
    except Exception as e:
        logger.warning(f"进入新品专区失败: {e}")
        return None


def select_today_new_products(shop_page) -> bool:
    """
    在新品专区中选择今日上新日期。
    左侧日期列表格式为 "X月X日"（如 "4月16日"），需精确匹配。
    返回是否成功选择。
    """
    from datetime import datetime
    now = datetime.now()
    # 生成匹配格式："4月16日"
    today_date = f"{now.month}月{now.day}日"

    try:
        result = shop_page.evaluate("""(todayDate) => {
            var all = document.querySelectorAll('a, div, span, li, button');
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                var txt = String(el.innerText || '').trim();
                if (txt === todayDate || txt.indexOf(todayDate) === 0) {
                    var r = el.getBoundingClientRect();
                    if (r.width > 5 && r.height > 5) {
                        el.click();
                        return {clicked: true, txt: txt};
                    }
                }
            }
            // 没找到今日日期，不兜底选其他日期
            return {clicked: false};
        }""", today_date)

        if result and result.get('clicked'):
            shop_page.wait_for_timeout(3000)
            logger.info(f"已选择今日上新: {result.get('txt')}")
            return True
        else:
            logger.info(f"今日（{today_date}）无上新商品")
            return False
    except Exception as e:
        logger.warning(f"选择上新日期失败: {e}")
        return False


def click_sort_by_sales(shop_page) -> bool:
    """
    在全店商品页点击"销量"排序按钮，使商品按销量从高到低排序。
    这样无销量（已售0或无标记）的商品会排在后面/前面（取决于排序方向）。
    """
    try:
        result = shop_page.evaluate("""() => {
            var all = document.querySelectorAll('a, div, span, li, button');
            for (var i = 0; i < all.length; i++) {
                var txt = String(all[i].innerText || '').trim();
                if (txt === '销量' || txt === '按销量') {
                    var r = all[i].getBoundingClientRect();
                    if (r.width > 10 && r.height > 10) {
                        all[i].click();
                        return {clicked: true, txt: txt};
                    }
                }
            }
            return {clicked: false};
        }""")
        if result and result.get('clicked'):
            shop_page.wait_for_timeout(3000)
            logger.info(f"已点击销量排序: {result.get('txt')}")
            return True
        else:
            logger.warning("未找到销量排序按钮")
            return False
    except Exception as e:
        logger.warning(f"点击销量排序失败: {e}")
        return False


def get_new_product_items(shop_page):
    """
    获取新品专区中当前显示的商品图片元素。
    与 get_shop_items 类似，但作用于新品区域。
    返回 (elements, selector) 元组。
    """
    return get_shop_items(shop_page)


def _extract_shop_url(detail_url: str) -> str:
    """从商品详情URL提取店铺URL"""
    import re
    # 1688商品URL格式: https://detail.1688.com/offer/xxx.html
    # 店铺URL格式: https://shop.1688.com/shop/xxx.html 或通过 companyId
    match = re.search(r"companyId=(\d+)", detail_url)
    if match:
        company_id = match.group(1)
        return f"https://shop.1688.com/shop/{company_id}.html"
    return None


def _scan_product_images(shop_page) -> list:
    """
    用 JS 扫描当前页面中所有产品图片，返回每张图的唯一标识（src）列表。
    仅返回 src 列表，不返回 ElementHandle（避免因滚动导致元素失效）。
    """
    return shop_page.evaluate("""() => {
        const results = [];
        const seen = new Set();
        for (const img of document.querySelectorAll('img')) {
            const src = img.getAttribute('src') || '';
            if (src.startsWith('data:') || src === '') continue;
            const r = img.getBoundingClientRect();
            if (r.width < 100 || r.height < 100) continue;
            if (r.width > 500 || r.height > 500) continue;
            // 排除侧边栏
            let node = img.parentElement;
            let inSidebar = false;
            while (node && node !== document.body) {
                const cls = (node.className || '').toLowerCase();
                if (/sider|sidebar|right.panel|detail.panel|cart.sider|od-cart|module-od/.test(cls)) {
                    inSidebar = true; break;
                }
                node = node.parentElement;
            }
            if (inSidebar) continue;
            if (seen.has(src)) continue;
            seen.add(src);
            results.push(src);
        }
        return results;
    }""") or []


def get_shop_items(shop_page):
    """
    获取全店商品列表页中所有商品图片元素（ElementHandle 列表）。
    只返回有真实 CDN URL 的图片（排除 data:image 懒加载占位符），
    排除侧边栏元素。
    返回 (elements, selector) 元组。
    """
    # 1. 等待页面容器出现
    for sel in [".entry-card", ".newofferlist", ".offerlist"]:
        try:
            shop_page.wait_for_selector(sel, timeout=15000)
            logger.info(f"检测到商品容器: {sel}")
            break
        except Exception:
            continue

    # 2. 逐步滚动到底部触发所有懒加载图片渲染，再滚回顶部
    try:
        shop_page.evaluate("""() => {
            return new Promise(resolve => {
                var scrolled = 0;
                var step = 500;
                var timer = setInterval(() => {
                    scrolled += step;
                    window.scrollTo(0, scrolled);
                    if (scrolled >= document.body.scrollHeight) {
                        clearInterval(timer);
                        window.scrollTo(0, 0);
                        resolve();
                    }
                }, 150);
            });
        }""")
        shop_page.wait_for_timeout(1000)
    except Exception:
        pass

    # 3. 扫描产品图片元素
    img_els = shop_page.evaluate_handle("""() => {
        const results = [];
        const seen = new Set();
        for (const img of document.querySelectorAll('img')) {
            const src = img.getAttribute('src') || '';
            if (src.startsWith('data:') || src === '') continue;
            const r = img.getBoundingClientRect();
            if (r.width < 100 || r.height < 100) continue;
            if (r.width > 500 || r.height > 500) continue;
            let node = img.parentElement;
            let inSidebar = false;
            while (node && node !== document.body) {
                const cls = (node.className || '').toLowerCase();
                if (/sider|sidebar|right.panel|detail.panel|cart.sider|od-cart|module-od/.test(cls)) {
                    inSidebar = true; break;
                }
                node = node.parentElement;
            }
            if (inSidebar) continue;
            if (seen.has(src)) continue;
            seen.add(src);
            results.push(img);
        }
        return results;
    }""")

    items = []
    try:
        count = shop_page.evaluate("els => els.length", img_els)
        logger.info(f"[DEBUG] 扫描到 {count} 个候选产品图（已过滤占位符和侧边栏）")
        for i in range(count):
            el = shop_page.evaluate_handle(f"els => els[{i}]", img_els)
            items.append(el.as_element())
    except Exception as e:
        logger.error(f"图片元素转换失败: {e}")

    if items:
        logger.info(f"当前视口找到 {len(items)} 个商品图片")
        return items, "product-img"

    logger.warning("当前页未找到任何商品图片，输出调试信息...")
    try:
        info = shop_page.evaluate("""() => {
            const allImgs = document.querySelectorAll('img');
            const imgInfo = Array.from(allImgs).slice(0, 10).map(img => {
                const r = img.getBoundingClientRect();
                return {
                    src: (img.getAttribute('src') || '').slice(0, 80),
                    w: Math.round(r.width), h: Math.round(r.height),
                    x: Math.round(r.x), y: Math.round(r.y),
                    parentTag: img.parentElement ? img.parentElement.tagName : '',
                    parentCls: (img.parentElement && img.parentElement.className || '').slice(0, 60)
                };
            });
            return { imgCount: allImgs.length, imgInfo, url: location.href };
        }""")
        logger.error(f"[DEBUG] URL: {info['url']}, 页面共 {info['imgCount']} 张图片")
        for im in info['imgInfo']:
            logger.error(f"  {im['w']}x{im['h']} parent=[{im['parentTag']}] cls='{im['parentCls']}' src={im['src']}")
    except Exception as e:
        logger.error(f"[DEBUG] 调试失败: {e}")

    return [], None


def go_to_next_page(shop_page) -> bool:
    """
    翻到下一页。
    用 JS 在 DOM 中找包含"下一页"文字的按钮（包含匹配，兼容"下一页 >"等变体），
    先 scrollIntoView 再 click()，不依赖元素是否在视口内可见。
    """
    try:
        result = shop_page.evaluate("""() => {
            var all = document.querySelectorAll('button, a, span, li, div');
            for (var i = 0; i < all.length; i++) {
                var txt = String(all[i].innerText || all[i].textContent || '').trim();
                if (txt.indexOf('下一页') !== -1 && txt.length < 20) {
                    var cls = String(all[i].className || '');
                    if (cls.indexOf('disabled') !== -1 || cls.indexOf('Disabled') !== -1) {
                        return {clicked: false, reason: 'disabled'};
                    }
                    all[i].scrollIntoView({block: 'center'});
                    all[i].click();
                    return {clicked: true, tag: all[i].tagName, txt: txt};
                }
            }
            return {clicked: false, reason: 'not-found'};
        }""")

        if result and result.get('clicked'):
            logger.info(f"翻页成功: <{result['tag']}> \"{result['txt']}\"")
            shop_page.wait_for_timeout(3000)
            return True
        elif result and result.get('reason') == 'disabled':
            logger.info("下一页按钮已禁用，已到最后一页")
            return False
        else:
            logger.info("未找到下一页按钮")
            return False
    except Exception as e:
        logger.warning(f"翻页异常: {e}")
        return False
