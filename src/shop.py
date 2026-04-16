import logging
import re
from src.utils import save_screenshot, random_delay
from src.selector_health import try_selectors

logger = logging.getLogger("1688-auto")

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
    ]
    for sel in SHOP_NAME_DIRECT_SELECTORS:
        name_els = page.query_selector_all(sel)
        for name_el in name_els:
            try:
                name_text = name_el.inner_text().strip()
                if not name_text or not _match_shop_name(name_text, shop_name):
                    continue
                logger.info(f"找到目标店铺商品，显示名: 「{name_text}」")
                # 向上最多 12 层，找到含商品详情链接的祖先，返回那个 <a>
                product_link = page.evaluate_handle("""el => {
                    let node = el;
                    for (let i = 0; i < 12; i++) {
                        node = node.parentElement;
                        if (!node) break;
                        const a = node.querySelector(
                            'a[href*="detail.1688.com"], a[href*="/offer/"]'
                        );
                        if (a) return a;
                    }
                    return null;
                }""", name_el)
                link_el = product_link.as_element()
                if link_el:
                    href = link_el.get_attribute("href") or ""
                    logger.info(f"找到商品详情链接: {href[:80]}")
                    return link_el

                # 没有 detail 链接，返回店铺名元素本身，由调用方点击其上方区域
                logger.info("未找到详情链接，将点击店铺名上方区域（商品图片位置）")
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


def _find_shop_item(page, shop_name: str, max_loads: int = 1):
    """
    在搜索结果页中找到目标店铺的第一个商品。
    每次滚动到底部后，等待页面高度实际增加（即新内容加载出来），
    才计为一次「加载」，最多加载 max_loads 次新内容。
    """
    # 等待首屏商品加载
    page.wait_for_timeout(2000)

    # 先扫描首屏
    card = _scan_current_cards(page, shop_name)
    if card:
        return card

    loads_done = 0
    while loads_done < max_loads:
        # 记录滚动前的高度和卡片数
        prev_height = page.evaluate("() => document.body.scrollHeight")

        # 滚动到底部
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")

        # 等待高度增加，最多等 8 秒（每 500ms 检测一次）
        new_content_loaded = False
        for _ in range(16):
            page.wait_for_timeout(500)
            new_height = page.evaluate("() => document.body.scrollHeight")
            if new_height > prev_height:
                new_content_loaded = True
                break

        if not new_content_loaded:
            logger.info("滚动后页面高度未变化，已到达结果末尾，停止加载")
            break

        loads_done += 1
        logger.info(f"新内容已加载 ({loads_done}/{max_loads})，扫描店铺...")

        card = _scan_current_cards(page, shop_name)
        if card:
            return card

    logger.warning(f"加载新内容 {loads_done} 次后仍未找到店铺: {shop_name}")
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
    logger.info("点击商品进入详情页...")
    with context.expect_page() as detail_page_info:
        try:
            href = card.get_attribute("href") if hasattr(card, "get_attribute") else None
            if href and ("detail.1688.com" in href or "/offer/" in href):
                # 直接是商品详情链接，点击它
                logger.info(f"点击详情链接: {href[:80]}")
                card.click()
            else:
                # 是店铺名元素，先滚入视口，再点击其上方的商品图片区域
                card.scroll_into_view_if_needed()
                result_page.wait_for_timeout(800)
                box = card.bounding_box()
                if not box:
                    raise RuntimeError("无法获取店铺名元素坐标")
                # 商品图片通常在店铺名上方约 100px 处
                click_x = box["x"] + box["width"] / 2
                click_y = box["y"] - 100
                logger.info(f"店铺名坐标: x={box['x']:.0f}, y={box['y']:.0f}，点击上方 y={click_y:.0f}")
                result_page.mouse.click(click_x, click_y)
        except Exception as e:
            raise RuntimeError(f"点击商品失败: {e}")

    detail_page = detail_page_info.value
    detail_page.wait_for_load_state("domcontentloaded")
    detail_page.wait_for_timeout(3000)
    logger.info(f"商品详情页: {detail_page.url}")

    # 在详情页找到进入全店的链接
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


def _clean_shop_url(url: str) -> str:
    """
    去掉全店商品页 URL 中的 offerId、spm、td_page_id 等参数，
    只保留干净的 offerlist.htm 地址，避免页面进入"相关商品"模式导致翻页控件被隐藏。
    """
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    parsed = urlparse(url)
    # 只保留必要参数，去掉可能影响页面模式的参数
    remove_params = {"offerId", "spm", "td_page_id"}
    params = parse_qs(parsed.query)
    cleaned = {k: v[0] for k, v in params.items() if k not in remove_params}
    new_query = urlencode(cleaned) if cleaned else ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", new_query, ""))


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

    # 用干净的 URL 重新加载（去掉 offerId 等参数，确保完整商品列表模式）
    clean_url = _clean_shop_url(shop_page.url)
    if clean_url != shop_page.url:
        logger.info(f"用干净URL重新加载: {clean_url}")
        shop_page.goto(clean_url, wait_until="domcontentloaded")
        shop_page.wait_for_timeout(3000)

    logger.info(f"全店商品页: {shop_page.url}")
    return shop_page


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

    # 2. 扫描产品图片元素
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
