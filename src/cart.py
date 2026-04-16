import logging
from src.utils import parse_price, save_screenshot, random_delay
from src.shop import get_shop_items, go_to_next_page, enter_new_product_zone, select_today_new_products, get_new_product_items, click_sort_by_sales
from src.selector_health import try_selectors, get_tracker
from src.retry import CircuitBreaker, is_page_alive, check_for_verification, wait_for_verification_clear, try_refresh_page

logger = logging.getLogger("1688-auto")

# 数量"+"按钮选择器（详情页，精确匹配数量控件旁的加号）
QUANTITY_PLUS_SELECTORS = [
    # Ant Design NumberInput（1688 2025 版）
    ".ant-input-number-handler-up",
    ".ant-input-number-handler-up-inner",
    # Fusion Design / Next
    ".next-number-picker-handler-up",
    ".number-picker-handler-up",
    "button.next-btn-number-plus",
    "[class*='numberPickerPlus']",
    "[class*='number-picker'] [class*='up']",
    "[class*='quantity'] [class*='plus']",
    "[class*='quantityPlus']",
    "[class*='stepperPlus']",
    "[class*='stepper'] [class*='plus']",
]

# "加采购车"按钮选择器（详情页）
ADD_TO_CART_SELECTORS = [
    "button:has-text('加入采购车')",
    "button:has-text('加采购车')",
    "a:has-text('加入采购车')",
    "a:has-text('加采购车')",
    "[class*='addCart']",
    "[class*='add-cart']",
    ".add-to-cart",
]

# 弹窗确认 / 关闭
POPUP_CONFIRM_SELECTORS = [
    "button:has-text('确定')",
    "button:has-text('确认')",
    ".dialog-confirm",
    "[class*='confirm']:visible",
]
POPUP_CLOSE_SELECTORS = [
    ".dialog-close",
    ".modal-close",
    "button:has-text('关闭')",
    "[class*='close']:visible",
    ".icon-close",
]

# 采购车总金额选择器
CART_AMOUNT_SELECTORS = [
    ".cart-total-price",
    "[class*='totalPrice']",
    "[class*='cartTotal']",
    ".purchase-cart-total",
    ".cart-amount",
    # 1688 采购车 2025 版
    "[class*='total-price']",
    "[class*='TotalPrice']",
    "[class*='settle'] [class*='price']",
    "[class*='settlement'] [class*='amount']",
    ".J_TotalPrice",
    "#J_TotalPrice",
]

# 商品价格选择器（商品列表卡片内）
ITEM_PRICE_SELECTORS = [
    ".price",
    "[class*='price']",
    ".offer-price",
    ".item-price",
]

# 跳过商品的标志
SKIP_KEYWORDS = ["询价", "联系客服", "已下架", "无货", "暂无报价"]


def _get_item_price(item_el) -> float:
    el = try_selectors(item_el, ITEM_PRICE_SELECTORS, "商品列表价格")
    if el:
        try:
            price = parse_price(el.inner_text().strip())
            if price > 0:
                return price
        except Exception:
            pass
    return 0.0


def _should_skip_item(item_el) -> bool:
    try:
        text = item_el.inner_text()
        for kw in SKIP_KEYWORDS:
            if kw in text:
                return True
    except Exception:
        pass
    return False


def _close_popup(page):
    el = try_selectors(page, POPUP_CLOSE_SELECTORS, "弹窗关闭按钮", check_visible=True)
    if el:
        try:
            el.click()
            page.wait_for_timeout(500)
            return True
        except Exception:
            pass
    return False


def _confirm_popup(page):
    el = try_selectors(page, POPUP_CONFIRM_SELECTORS, "弹窗确认按钮", check_visible=True)
    if el:
        try:
            el.click()
            page.wait_for_timeout(500)
            return True
        except Exception:
            pass
    return False


def capture_cart_url(page) -> str:
    """
    在 1688 首页（登录后）获取采购车链接的完整 URL。
    先确保在首页并等待页面完全加载，然后多次尝试查找采购车链接。
    返回采购车 URL 字符串，失败返回空字符串。
    """
    # 确保在 1688 首页
    try:
        if '1688.com' not in page.url or 'detail' in page.url or 'offer' in page.url:
            page.goto("https://www.1688.com", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
    except Exception:
        pass

    # 等待页面完全加载后再找采购车链接（最多等 15 秒，每 2 秒试一次）
    coord = None
    for attempt in range(8):
        coord = _find_cart_link(page)
        if coord and coord.get('href'):
            break
        logger.info(f"首页查找采购车链接...（第{attempt+1}次）")
        page.wait_for_timeout(2000)

    if not coord or not coord.get('href'):
        logger.warning("首页未找到采购车链接（多次尝试后仍未找到）")
        return ""

    logger.info(f"获取采购车URL: 点击 \"{coord['txt']}\" href={coord['href']}")
    try:
        with page.context.expect_page(timeout=10000) as new_page_info:
            page.mouse.click(coord['x'], coord['y'])
        cart_page = new_page_info.value
        cart_page.wait_for_load_state("domcontentloaded")
        cart_page.wait_for_timeout(2000)
        cart_url = cart_page.url
        logger.info(f"采购车URL已获取: {cart_url}")
        cart_page.close()
        return cart_url
    except Exception as e:
        logger.warning(f"获取采购车URL失败: {e}")
        return ""


# 模块级变量，存储采购车 URL（由 main.py 在启动时设置）
_cart_url = ""


def set_cart_url(url: str):
    """设置采购车 URL（由 main.py 调用）。"""
    global _cart_url
    _cart_url = url
    logger.info(f"采购车URL已设置: {url}")


def _open_cart_in_new_tab(context):
    """用已保存的采购车 URL 在新标签页中打开采购车。"""
    if not _cart_url:
        logger.error("采购车URL未设置，无法打开")
        return None

    try:
        cart_page = context.new_page()
        cart_page.goto(_cart_url, wait_until="domcontentloaded")
        cart_page.wait_for_timeout(3000)
        if 'cart' in cart_page.url and '1688' in cart_page.url:
            logger.info(f"采购车已打开（新标签）: {cart_page.url}")
            return cart_page
        else:
            logger.warning(f"采购车URL跳转异常: {cart_page.url}")
            cart_page.close()
    except Exception as e:
        logger.warning(f"打开采购车失败: {e}")

    return None


def _find_cart_link(page):
    """在当前页面查找采购车链接，返回坐标或 None。"""
    try:
        return page.evaluate("""() => {
            var all = document.querySelectorAll('a');
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                var txt = String(el.innerText || el.textContent || '').trim();
                var href = el.getAttribute('href') || '';
                // 找文字为"采购车"且 href 含 cart 的 <a> 链接
                if (txt.indexOf('采购车') !== -1 && txt.length < 10
                    && href.indexOf('cart') !== -1
                    && txt.indexOf('加') === -1) {
                    var r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        return {x: r.x + r.width / 2, y: r.y + r.height / 2, href: href, txt: txt};
                    }
                }
            }
            // 兜底：找任何 href 含 cart.1688.com 的可见 <a>
            for (var j = 0; j < all.length; j++) {
                var el2 = all[j];
                var href2 = el2.getAttribute('href') || '';
                if (href2.indexOf('cart.1688.com') !== -1) {
                    var r2 = el2.getBoundingClientRect();
                    if (r2.width > 0 && r2.height > 0) {
                        return {x: r2.x + r2.width / 2, y: r2.y + r2.height / 2, href: href2, txt: String(el2.innerText || '').trim()};
                    }
                }
            }
            return null;
        }""")
    except Exception:
        return None


def _get_detail_page_price(detail_page) -> float:
    """从商品详情页读取当前选中规格的价格。"""
    try:
        price = detail_page.evaluate("""() => {
            // 优先找选中规格后显示的价格（通常在规格区域附近）
            const selectors = [
                '[class*="price"] [class*="text"]',
                '[class*="priceText"]',
                '[class*="sku"] [class*="price"]',
                '[class*="Price"]',
                '.price-text',
                '.offer-price',
                '[class*="price"]',
            ];
            for (const sel of selectors) {
                for (const el of document.querySelectorAll(sel)) {
                    const txt = (el.innerText || '').trim();
                    if (!txt) continue;
                    const m = txt.match(/[¥￥]?\s*([\d,]+\.?\d*)/);
                    if (m) {
                        const v = parseFloat(m[1].replace(/,/g, ''));
                        if (v > 0 && v < 100000) return v;
                    }
                }
            }
            return 0;
        }""")
        return float(price) if price else 0.0
    except Exception as e:
        logger.debug(f"读取详情页价格失败: {e}")
        return 0.0


def verify_cart_amount(context) -> tuple:
    """
    打开采购车新标签 → 用鼠标点击全选 → 读取收银台总金额 → 关闭标签。
    返回 (金额, 是否成功)。金额 <= 0 表示失败。
    不影响当前页面。
    """
    logger.info("--- 开始校准采购车金额 ---")

    # 1. 用已保存的 URL 在新标签打开采购车
    cart_page = _open_cart_in_new_tab(context)
    if not cart_page:
        return -1.0, False

    try:
        # 2. 用真实鼠标点击"全选"
        _uncheck_all(cart_page)  # 先确保取消全选
        cart_page.wait_for_timeout(500)
        coord = _mouse_click_select_all(cart_page)
        if coord:
            cart_page.mouse.click(coord['x'], coord['y'])
            cart_page.wait_for_timeout(2000)
            logger.info("已鼠标点击全选")
        else:
            logger.warning("未找到全选按钮")

        # 3. 读取收银台总金额
        amount = _read_bottom_bar_amount(cart_page)
        if amount > 0:
            logger.info(f"采购车校准金额: ¥{amount:.2f}")
            return amount, True
        else:
            logger.warning("未能读取采购车收银台金额")
            return -1.0, False
    finally:
        # 关闭采购车标签，不影响其他页面
        try:
            cart_page.close()
        except Exception:
            pass


def _open_item_detail(context, item_el, shop_page):
    """
    通过真实鼠标点击商品图片打开商品详情页。
    使用 page.mouse.click(x, y) 而非 element.click()，
    确保触发页面上的 JS 事件监听器。
    """
    # 1. 将元素滚动到视口内
    try:
        item_el.scroll_into_view_if_needed()
        shop_page.wait_for_timeout(500)
    except Exception:
        pass

    # 2. 获取元素在当前视口中的坐标
    box = None
    try:
        box = item_el.bounding_box()
    except Exception:
        pass

    if not box or box['width'] < 1 or box['height'] < 1:
        logger.warning("无法获取元素坐标，跳过此商品")
        return None

    cx = box['x'] + box['width'] / 2
    cy = box['y'] + box['height'] / 2
    logger.info(f"模拟鼠标点击图片坐标: ({cx:.0f}, {cy:.0f}), 尺寸 {box['width']:.0f}x{box['height']:.0f}")

    # 3. 尝试捕获新标签页（等 6 秒）
    try:
        with context.expect_page(timeout=6000) as detail_info:
            shop_page.mouse.click(cx, cy)
        detail_page = detail_info.value
        detail_page.wait_for_load_state("domcontentloaded")
        detail_page.wait_for_timeout(2000)
        logger.info(f"打开商品详情页（新标签）: {detail_page.url}")
        return detail_page
    except Exception:
        pass

    # 4. 没有新标签：检查当前页是否已跳转
    try:
        shop_page.wait_for_url(
            lambda url: "detail.1688.com" in url or "/offer/" in url,
            timeout=8000
        )
        shop_page.wait_for_load_state("domcontentloaded")
        shop_page.wait_for_timeout(2000)
        logger.info(f"打开商品详情页（当前页跳转）: {shop_page.url}")
        return shop_page
    except Exception:
        pass

    logger.warning(f"点击 ({cx:.0f},{cy:.0f}) 后页面未跳转，跳过此商品")
    return None


def _select_first_sku(detail_page) -> bool:
    """
    若详情页有规格选项（SKU），点击第一个可用的规格，
    避免未选规格直接加购导致跳转错误。
    使用 page.mouse.click() 触发真实鼠标事件，确保 React 合成事件正确触发。
    """
    try:
        # 用 JS 找第一个可见的规格选项坐标，返回 {x, y, cls}
        info = detail_page.evaluate("""() => {
            const selectors = [
                '[class*="sku"] [class*="item"]:not([class*="disabled"]):not([class*="soldout"])',
                '[class*="spec"] [class*="item"]:not([class*="disabled"])',
                '[class*="prop"] [class*="item"]:not([class*="disabled"])',
                '[class*="SkuItem"]:not([class*="disabled"])',
                '[class*="skuItem"]:not([class*="disabled"])',
            ];
            for (const sel of selectors) {
                const items = document.querySelectorAll(sel);
                for (const item of items) {
                    const r = item.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        return { x: r.x + r.width / 2, y: r.y + r.height / 2, cls: item.className };
                    }
                }
            }
            return null;
        }""")
        if info:
            logger.info(f"规格坐标 ({info['x']:.0f}, {info['y']:.0f}) class={str(info['cls'])[:60]}")
            detail_page.mouse.click(info['x'], info['y'])
            detail_page.wait_for_timeout(600)
            return True
    except Exception as e:
        logger.debug(f"规格选择异常: {e}")
    return False


def _scroll_quantity_into_view(detail_page) -> bool:
    """将数量输入框滚动到视口中央，返回是否找到并滚动成功。"""
    try:
        found = detail_page.evaluate("""() => {
            const sel = '.ant-input-number-input, [class*="number-picker"] input, [class*="quantity"] input, [class*="stepper"] input';
            const inp = document.querySelector(sel);
            if (!inp) return false;
            inp.scrollIntoView({ behavior: 'instant', block: 'center' });
            return true;
        }""")
        if found:
            detail_page.wait_for_timeout(300)  # 等待滚动完成
        return bool(found)
    except Exception:
        return False


def _click_quantity_plus(detail_page) -> bool:
    """
    在详情页将数量从默认 0 调整为 1。
    规格选项较多时数量框可能被推出视口，操作前先滚动到位。
    策略一：找"+"按钮用真实鼠标点击一次（0→1）。
    策略二：找数量输入框，用键盘操作（focus → Ctrl+A → type "1" → Tab）确保 React 状态更新。
    """
    # 先确保数量控件在视口内
    _scroll_quantity_into_view(detail_page)

    # 策略一：hover 到输入框让 Ant Design handler 渲染出来，再找"+"坐标
    try:
        inp_info = detail_page.evaluate("""() => {
            const inp = document.querySelector(
                '.ant-input-number-input, [class*="number-picker"] input, [class*="quantity"] input'
            );
            if (!inp) return null;
            const r = inp.getBoundingClientRect();
            // 若仍在视口外则放弃（scrollIntoView 应已处理）
            if (r.width < 1 || r.bottom < 0 || r.top > window.innerHeight) return null;
            return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
        }""")
        if inp_info:
            detail_page.mouse.move(inp_info['x'], inp_info['y'])
            detail_page.wait_for_timeout(300)
    except Exception:
        pass

    # 找"+"按钮坐标（Ant Design handler-up 或文字为"+"的兄弟节点）
    try:
        plus_info = detail_page.evaluate("""() => {
            // Ant Design: .ant-input-number-handler-up
            const antPlus = document.querySelector('.ant-input-number-handler-up');
            if (antPlus) {
                const r = antPlus.getBoundingClientRect();
                if (r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= window.innerHeight)
                    return { x: r.x + r.width / 2, y: r.y + r.height / 2, src: 'ant-handler-up' };
            }
            // 找文字为"+"的兄弟节点
            const inputs = document.querySelectorAll(
                'input[type="number"], input[class*="number"], [class*="quantity"] input, [class*="number-picker"] input'
            );
            for (const inp of inputs) {
                let node = inp.nextElementSibling;
                for (let i = 0; i < 4; i++) {
                    if (!node) break;
                    const txt = (node.innerText || node.textContent || '').trim();
                    if (txt === '+') {
                        const r = node.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= window.innerHeight)
                            return { x: r.x + r.width / 2, y: r.y + r.height / 2, src: 'sibling' };
                    }
                    node = node.nextElementSibling;
                }
                // 往前找
                node = inp.previousElementSibling;
                for (let i = 0; i < 4; i++) {
                    if (!node) break;
                    const txt = (node.innerText || node.textContent || '').trim();
                    if (txt === '+') {
                        const r = node.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= window.innerHeight)
                            return { x: r.x + r.width / 2, y: r.y + r.height / 2, src: 'prev-sibling' };
                    }
                    node = node.previousElementSibling;
                }
            }
            return null;
        }""")
        if plus_info:
            logger.info(f"点击+按钮坐标 ({plus_info['x']:.0f}, {plus_info['y']:.0f}) 来源={plus_info['src']}")
            detail_page.mouse.click(plus_info['x'], plus_info['y'])
            detail_page.wait_for_timeout(400)
            return True
    except Exception as e:
        logger.debug(f"找+按钮坐标失败: {e}")

    # 策略二：键盘操作输入框（Ctrl+A → type "2" → Tab → 点空白处确认）
    try:
        inp_coord = detail_page.evaluate("""() => {
            const inp = document.querySelector(
                '.ant-input-number-input, input[class*="number"], [class*="quantity"] input, [class*="number-picker"] input'
            );
            if (!inp) return null;
            const r = inp.getBoundingClientRect();
            if (r.width < 1 || r.height < 1) return null;
            // 如果元素仍在视口外（scrollIntoView 可能未完成），报告实际位置供调试
            if (r.bottom < 0 || r.top > window.innerHeight) return null;
            return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
        }""")
        if inp_coord:
            # 点击聚焦
            detail_page.mouse.click(inp_coord['x'], inp_coord['y'])
            detail_page.wait_for_timeout(200)
            # 全选当前值并替换
            detail_page.keyboard.press("Control+a")
            detail_page.wait_for_timeout(100)
            detail_page.keyboard.type("1")
            detail_page.wait_for_timeout(200)
            # Tab 触发 onBlur / onChange
            detail_page.keyboard.press("Tab")
            detail_page.wait_for_timeout(300)
            # 点击页面空白处再次确认 onBlur（取商品标题区域）
            detail_page.mouse.click(400, 200)
            detail_page.wait_for_timeout(300)
            logger.info(f"键盘输入数量 1 (坐标 {inp_coord['x']:.0f},{inp_coord['y']:.0f})")
            return True
    except Exception as e:
        logger.debug(f"键盘输入数量失败: {e}")

    # 调试信息
    try:
        candidates = detail_page.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll('*')) {
                const txt = (el.textContent || '').trim();
                if (txt !== '+') continue;
                const r = el.getBoundingClientRect();
                if (r.width < 1 || r.height < 1) continue;
                out.push('[' + el.tagName + '] cls=' + el.className.slice(0,50)
                       + ' x=' + Math.round(r.x) + ' y=' + Math.round(r.y));
            }
            return out;
        }""")
        logger.error(f"[DEBUG] 页面中文字为'+'的元素 ({len(candidates)} 个):")
        for c in candidates:
            logger.error(f"  {c}")
        inputs = detail_page.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll('input')) {
                const r = el.getBoundingClientRect();
                if (r.width < 1) continue;
                out.push('[INPUT] type=' + el.type + ' cls=' + el.className.slice(0,40)
                       + ' val=' + el.value + ' x=' + Math.round(r.x) + ' y=' + Math.round(r.y));
            }
            return out;
        }""")
        logger.error(f"[DEBUG] 页面中 input 元素 ({len(inputs)} 个):")
        for i in inputs:
            logger.error(f"  {i}")
    except Exception as e:
        logger.error(f"[DEBUG] 调试失败: {e}")

    logger.warning("未找到数量输入框或+按钮")
    return False


def _click_add_to_cart(detail_page) -> bool:
    """
    在详情页点击「加采购车」按钮。
    优先找 <button> 标签，避免点到导致页面跳转的 <a> 链接。
    """
    # 先用 JS 扫描所有候选元素，打印出来供调试，同时按优先级返回最佳元素
    candidates = detail_page.evaluate("""() => {
        const results = [];
        for (const el of document.querySelectorAll('button, a, div, span')) {
            const txt = (el.innerText || '').trim();
            if (!txt.includes('加') || !txt.includes('采购车')) continue;
            if (txt.includes('去采购车') || txt.includes('查看采购车')) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 1 || r.height < 1) continue;
            results.push({
                tag: el.tagName,
                cls: el.className,
                txt: txt,
                href: el.href || '',
                x: Math.round(r.x), y: Math.round(r.y)
            });
        }
        return results;
    }""")

    logger.info(f"[DEBUG] 找到 {len(candidates)} 个「加采购车」候选元素:")
    for c in candidates:
        logger.info(f"  [{c['tag']}] class={c['cls'][:40]} | txt={c['txt']} | href={c['href'][:60]}")

    # 优先顺序：BUTTON > 无 href 的 DIV/SPAN > 有 href 的 A（最后选，避免跳转）
    def priority(c):
        if c['tag'] == 'BUTTON':
            return 0
        if c['tag'] in ('DIV', 'SPAN') and not c['href']:
            return 1
        if c['tag'] == 'A' and not c['href']:
            return 2
        return 3  # 有 href 的 <a>，最后才用

    if not candidates:
        logger.warning("未找到加采购车按钮")
        return False

    candidates.sort(key=priority)
    best = candidates[0]
    logger.info(f"选择点击: [{best['tag']}] {best['txt']} (priority={priority(best)})")

    try:
        # 用坐标点击，避免选择器匹配歧义
        detail_page.mouse.click(best['x'] + 10, best['y'] + 10)
        detail_page.wait_for_timeout(1500)
        return True
    except Exception as e:
        logger.warning(f"点击加采购车失败: {e}")
        return False


def add_item_to_cart(context, item_el, shop_page=None) -> float:
    """
    完整的加购流程：
    1. 点击商品图片/标题，打开详情页（新标签或当前页跳转）
    2. 选择规格
    3. 调整数量为 1
    4. 读取商品价格
    5. 点击"加入采购车"
    6. 处理弹窗，关闭/返回详情页

    返回值：加购成功返回商品价格（>0），失败返回 0.0
    """
    detail_page = None
    current_page_navigated = False  # 标记是否是当前页跳转（需要导航回去）
    try:
        detail_page = _open_item_detail(context, item_el, shop_page)

        if detail_page is None:
            logger.warning("未能打开商品详情页，跳过此商品")
            return 0.0

        # 判断是否是 shop_page 自身跳转过去的
        if shop_page and detail_page is shop_page:
            current_page_navigated = True

        # 检测 404 / 无效页面，直接跳过
        url = detail_page.url
        if "404" in url or "error" in url.lower() or "detail.1688.com" not in url:
            logger.warning(f"详情页无效，跳过: {url}")
            return 0.0

        # 等页面主体加载完
        try:
            detail_page.wait_for_selector("div, section", timeout=5000)
        except Exception:
            pass

        # 二次确认：检查页面标题是否包含 404 字样
        title = detail_page.title()
        if "404" in title or "找不到" in title:
            logger.warning(f"详情页标题异常，跳过: {title}")
            return 0.0

        # 若有规格选项先选规格
        _select_first_sku(detail_page)
        random_delay(0.3, 0.6)

        # 页面默认数量为 0，必须调整为 1 才能加购
        if not _click_quantity_plus(detail_page):
            logger.warning("未能调整数量为1，加购可能失败")
        random_delay(0.3, 0.6)

        # 读取商品价格（选完规格和数量后读取，更准确）
        item_price = _get_detail_page_price(detail_page)
        logger.info(f"详情页商品价格: ¥{item_price:.2f}")

        # 点击"加入采购车"
        success = _click_add_to_cart(detail_page)
        if not success:
            save_screenshot(detail_page, "add_cart_btn_not_found")
            return 0.0

        # 处理弹窗
        random_delay(0.5, 1.0)
        _confirm_popup(detail_page)
        random_delay(0.3, 0.6)
        _close_popup(detail_page)

        return item_price

    except Exception as e:
        logger.warning(f"加入采购车失败: {e}")
        if detail_page:
            save_screenshot(detail_page, "add_cart_error")
        return 0.0
    finally:
        if detail_page:
            # 只关闭新标签页；当前页跳转则导航回 shop_page
            if current_page_navigated and shop_page:
                try:
                    shop_page.go_back()
                    shop_page.wait_for_load_state("domcontentloaded")
                    shop_page.wait_for_timeout(1500)
                except Exception:
                    try:
                        shop_page.goto(shop_page.url, wait_until="domcontentloaded")
                        shop_page.wait_for_timeout(2000)
                    except Exception:
                        pass
            else:
                try:
                    detail_page.close()
                except Exception:
                    pass


def _fill_cart_from_current_page(context, shop_page, cart_config, added_count, local_amount,
                                  last_verified_amount, verify_interval,
                                  progress_callback=None, cancel_check=None, label=""):
    """
    从当前页面的商品列表中加购，翻页直到达到目标或无更多商品。
    返回 (added_count, local_amount)。
    """
    target = cart_config.get("target_amount", 10000)
    strategy = cart_config.get("amount_strategy", "not_exceed")
    max_items = cart_config.get("max_items", 200)
    page_num = 1
    prefix = f"[{label}] " if label else ""

    breaker = CircuitBreaker(threshold=5)

    while added_count < max_items:
        if cancel_check and cancel_check():
            logger.info(f"{prefix}收到取消指令，停止加购")
            break

        if breaker.should_abort():
            logger.error(f"{prefix}[熔断] 连续失败 {breaker.consecutive_failures} 次，中止")
            break

        if breaker.level >= 1 and breaker.consecutive_failures % 5 == 0:
            try_refresh_page(shop_page)

        items, item_sel = get_shop_items(shop_page)
        if not items:
            logger.warning(f"{prefix}第{page_num}页未找到商品，停止")
            break

        logger.info(f"{prefix}第{page_num}页共 {len(items)} 个商品")

        # 优先采购无销量商品：将无销量的排到前面
        def _has_sales(el):
            try:
                txt = el.inner_text() or ""
                return "已售" in txt
            except Exception:
                return False

        items_sorted = sorted(items, key=lambda el: (1 if _has_sales(el) else 0))

        for item_el in items_sorted:
            if cancel_check and cancel_check():
                return added_count, local_amount
            if added_count >= max_items:
                return added_count, local_amount
            if breaker.should_abort():
                break
            if _should_skip_item(item_el):
                continue

            if local_amount >= target:
                logger.info(f"{prefix}本地累计 ¥{local_amount:.2f} 已达目标 ¥{target}")
                return added_count, local_amount

            if strategy == "not_exceed":
                est_price = _get_item_price(item_el)
                if est_price > 0 and local_amount + est_price > target:
                    continue

            if check_for_verification(shop_page):
                wait_for_verification_clear(shop_page)

            item_price = add_item_to_cart(context, item_el, shop_page=shop_page)
            if item_price > 0:
                added_count += 1
                local_amount += item_price
                breaker.record_success()
                logger.info(f"{prefix}[{added_count}] 商品 ¥{item_price:.2f} | 累计 ¥{local_amount:.2f} / ¥{target}")
                print(f"  {prefix}已加入 {added_count} 件 | ¥{item_price:.2f} | 累计 ¥{local_amount:.2f} / ¥{target}")
                if progress_callback:
                    try:
                        progress_callback(added_count, item_price, local_amount, target, page_num)
                    except Exception:
                        pass
            else:
                breaker.record_failure()

            if local_amount - last_verified_amount >= verify_interval:
                logger.info(f"{prefix}触发校准")
                real_amount, success = verify_cart_amount(context)
                if success and real_amount > 0:
                    local_amount = real_amount
                    last_verified_amount = real_amount
                else:
                    last_verified_amount = local_amount
                if local_amount >= target:
                    return added_count, local_amount

            random_delay(0.5, 1.5)

        logger.info(f"{prefix}第{page_num}页完毕，尝试翻页...")
        if not go_to_next_page(shop_page):
            logger.info(f"{prefix}已到最后一页")
            break
        page_num += 1
        random_delay(1.5, 3.0)

    return added_count, local_amount


def run_cart_filling(context, shop_page, cart_config: dict, progress_callback=None, cancel_check=None):
    """
    主循环：遍历全店商品，逐个打开详情页加入采购车，直到达到目标金额。

    金额追踪策略：
    - 程序本地累加每次加购的商品价格（local_amount）
    - 每累计约 ¥500 时，在新标签页打开采购车全选读取实际金额校准（不影响当前店铺页）
    - 当采购车总金额接近目标金额时停止
    """
    target = cart_config.get("target_amount", 10000)
    strategy = cart_config.get("amount_strategy", "not_exceed")
    max_items = cart_config.get("max_items", 200)
    purchase_mode = cart_config.get("purchase_mode", "normal")
    verify_interval = 500  # 每累计约 ¥500 校准一次

    logger.info(f"开始填充采购车 | 目标金额: ¥{target} | 策略: {strategy} | 模式: {purchase_mode}")

    added_count = 0
    local_amount = 0.0          # 程序本地累计金额
    last_verified_amount = 0.0  # 上次校准时的金额
    page_num = 1

    # 新品采购模式：先进新品专区采购，不足再回全部商品
    if purchase_mode == "new_product":
        logger.info("新品采购模式：优先采购当日上新商品")
        shop_url = shop_page.url
        new_product_page = None

        has_new = False
        new_product_page = enter_new_product_zone(context, shop_page)
        if new_product_page:
            has_new = select_today_new_products(new_product_page)

        if has_new and new_product_page:
            # 有今日上新，在新品页面采购
            added_count, local_amount = _fill_cart_from_current_page(
                context, new_product_page, cart_config, added_count, local_amount,
                last_verified_amount, verify_interval, progress_callback, cancel_check,
                label="新品"
            )

            # 关闭新品标签页（如果是新开的）
            if new_product_page != shop_page:
                try:
                    new_product_page.close()
                    logger.info("已关闭新品专区标签页")
                except Exception:
                    pass

            if local_amount >= target or added_count >= max_items:
                logger.info(f"新品采购已满足目标: {added_count} 件 ¥{local_amount:.2f}")
                return added_count

            logger.info(f"新品采购后: {added_count} 件 ¥{local_amount:.2f}，不足目标 ¥{target}，转入全部商品")
        else:
            logger.info("今日无上新或未能进入新品专区，按正常模式采购全部商品")
            # 关闭新品标签页（如果打开了但没有今日上新）
            if new_product_page and new_product_page != shop_page:
                try:
                    new_product_page.close()
                except Exception:
                    pass

        # 回到全部商品页
        try:
            shop_page.goto(shop_url, wait_until="domcontentloaded")
            shop_page.wait_for_timeout(3000)
        except Exception:
            pass

    # 全部商品采购前，点击销量排序（无销量商品优先）
    logger.info("点击销量排序，优先采购无销量商品...")
    click_sort_by_sales(shop_page)

    # 全部商品采购（正常模式直接走这里，新品模式不足时也走这里）
    added_count, local_amount = _fill_cart_from_current_page(
        context, shop_page, cart_config, added_count, local_amount,
        last_verified_amount, verify_interval, progress_callback, cancel_check
    )

    logger.info(f"采购车填充完成，共加入 {added_count} 件商品，本地累计: ¥{local_amount:.2f}")
    return added_count


# ─── 结算相关 ──────────────────────────────────────────────────────────────────


def _read_cart_items(cart_page) -> list:
    """
    读取采购车页面中所有商品条目，返回列表：
    [{ index: int, name: str, price: float }, ...]
    index 是该 TBODY 在页面中的顺序（从 0 开始），用于后续勾选操作。

    1688 采购车页面结构：
    - 每个商品是一个 <TBODY>（含图片 70x70、商品名、规格、价格）
    - 每个 SKU 行是 <TR class="item--container--xxx">
    - 价格是纯数字（无 ¥ 符号），最后一个数字是小计
    """
    try:
        items = cart_page.evaluate("""() => {
            var results = [];
            // 找所有含商品图片（70x70左右）的 TBODY
            var tbodies = document.querySelectorAll('tbody');
            var idx = 0;
            for (var i = 0; i < tbodies.length; i++) {
                var tb = tbodies[i];
                var r = tb.getBoundingClientRect();
                // 跳过不可见或太小的
                if (r.width < 400 || r.height < 50) continue;

                var txt = (tb.innerText || '').trim();
                if (txt.length < 10) continue;

                // 提取所有数字（价格格式：304.00）
                var nums = txt.match(/\\d+\\.\\d{2}/g);
                if (!nums || nums.length === 0) continue;

                // 小计是最后一个价格数字
                var price = parseFloat(nums[nums.length - 1]);
                if (price <= 0) continue;

                // 提取商品名（第一行较长的文字，排除纯数字行）
                var lines = txt.split('\\n').map(function(l) { return l.trim(); }).filter(function(l) { return l.length > 3; });
                var name = '';
                for (var j = 0; j < lines.length; j++) {
                    var line = lines[j];
                    // 跳过纯数字、"再选一款"等
                    if (/^[\\d.,\\s]+$/.test(line)) continue;
                    if (line.indexOf('再选') !== -1) continue;
                    if (line.indexOf('混批') !== -1 || line.length > 8) {
                        name = line.slice(0, 40);
                        break;
                    }
                }

                results.push({ index: idx, tbodyIndex: i, name: name, price: price });
                idx++;
            }
            return results;
        }""")
        return items or []
    except Exception as e:
        logger.warning(f"读取采购车商品列表失败: {e}")
        return []


def _group_items_for_checkout(items: list, limit: float = 500.0, shipping_reserve: float = 15.0) -> list:
    """
    将商品分组，每组商品总价 + 预留运费不超过 limit。
    预留 shipping_reserve 元运费空间（商品小计不含运费）。
    贪心策略：按价格降序排列，依次尝试放入当前组，放不下则开新组。
    返回 [[item, item, ...], [item, ...], ...]
    """
    effective_limit = limit - shipping_reserve
    # 按价格降序排序
    sorted_items = sorted(items, key=lambda x: x['price'], reverse=True)
    groups = []

    for item in sorted_items:
        placed = False
        # 尝试放入已有的某个组（优先放入剩余空间最小但能放下的组）
        best_group = None
        best_remaining = float('inf')
        for group in groups:
            group_total = sum(it['price'] for it in group)
            remaining = effective_limit - group_total
            if item['price'] <= remaining and remaining < best_remaining:
                best_group = group
                best_remaining = remaining
                placed = True
        if placed and best_group is not None:
            best_group.append(item)
        else:
            groups.append([item])

    return groups


def _mouse_click_select_all(cart_page):
    """用真实鼠标点击底部栏的'全选'复选框。"""
    coord = cart_page.evaluate("""() => {
        // 底部栏的全选（class 含 bottom-bar 或 sticky）
        var containers = document.querySelectorAll('[class*="bottom-bar"], [class*="sticky"]');
        for (var i = 0; i < containers.length; i++) {
            var cb = containers[i].querySelector('input[type="checkbox"]');
            if (cb) {
                var r = cb.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    return {x: r.x + r.width / 2, y: r.y + r.height / 2, checked: cb.checked};
                }
                // checkbox 可能被隐藏，找它的 label
                var label = cb.closest('label') || cb.parentElement;
                if (label) {
                    var lr = label.getBoundingClientRect();
                    if (lr.width > 0 && lr.height > 0) {
                        return {x: lr.x + lr.width / 2, y: lr.y + lr.height / 2, checked: cb.checked};
                    }
                }
            }
        }
        return null;
    }""")
    return coord


def _uncheck_all(cart_page):
    """用真实鼠标取消采购车中所有商品的勾选。"""
    try:
        coord = _mouse_click_select_all(cart_page)
        if not coord:
            logger.warning("未找到全选复选框")
            return

        if coord['checked']:
            # 当前全选，点一次取消
            cart_page.mouse.click(coord['x'], coord['y'])
            cart_page.wait_for_timeout(2000)
        else:
            # 可能部分选中，先全选再取消
            cart_page.mouse.click(coord['x'], coord['y'])
            cart_page.wait_for_timeout(2000)
            cart_page.mouse.click(coord['x'], coord['y'])
            cart_page.wait_for_timeout(2000)

        logger.info("已取消全部勾选")
    except Exception as e:
        logger.warning(f"取消全选失败: {e}")


def _get_item_checkbox_coords(cart_page) -> list:
    """获取每个商品 TBODY 内复选框的坐标列表。"""
    return cart_page.evaluate("""() => {
        var tbodies = document.querySelectorAll('tbody');
        var results = [];
        for (var i = 0; i < tbodies.length; i++) {
            var tb = tbodies[i];
            var r = tb.getBoundingClientRect();
            if (r.width < 400 || r.height < 50) continue;
            var txt = (tb.innerText || '').trim();
            if (txt.length < 10) continue;
            var nums = txt.match(/\\d+\\.\\d{2}/g);
            if (!nums || nums.length === 0) continue;

            var cb = tb.querySelector('input[type="checkbox"]');
            if (cb) {
                var cr = cb.getBoundingClientRect();
                // checkbox 可能尺寸为 0（被 CSS 隐藏），找它的 label
                if (cr.width < 1 || cr.height < 1) {
                    var label = cb.closest('label') || cb.parentElement;
                    if (label) cr = label.getBoundingClientRect();
                }
                if (cr.width > 0 && cr.height > 0) {
                    results.push({
                        x: cr.x + cr.width / 2,
                        y: cr.y + cr.height / 2,
                        checked: cb.checked
                    });
                    continue;
                }
            }
            // 兜底：用 TBODY 最左侧区域（checkbox 通常在最左边）
            results.push({x: r.x + 15, y: r.y + r.height / 2, checked: false});
        }
        return results;
    }""") or []


def _mouse_click_item(cart_page, coord):
    """用真实鼠标点击单个商品的复选框，等待服务器响应。"""
    cart_page.mouse.click(coord['x'], coord['y'])
    # 等待服务器交互完成（加载转圈消失）
    cart_page.wait_for_timeout(2000)


def _read_bottom_bar_amount(cart_page) -> float:
    """读取底部收银台的实时金额。"""
    try:
        result = cart_page.evaluate("""() => {
            // 底部栏 class 含 bottom-bar 或 sticky
            var bars = document.querySelectorAll('[class*="bottom-bar"], [class*="sticky"], [class*="totalInfo"]');
            for (var i = 0; i < bars.length; i++) {
                var txt = (bars[i].innerText || '').trim();
                // 找 ¥ 后面的金额
                var m = txt.match(/[¥￥]\\s*([\\d,]+\\.?\\d*)/);
                if (m) {
                    var v = parseFloat(m[1].replace(/,/g, ''));
                    if (v >= 0) return v;
                }
            }
            return -1;
        }""")
        return float(result) if result is not None else -1.0
    except Exception:
        return -1.0


def _read_checkout_amount(cart_page) -> float:
    """读取当前勾选商品的结算金额（收银台显示的合计）。"""
    try:
        result = cart_page.evaluate("""() => {
            const keywords = ['合计', '总计', '结算金额', '应付金额', '商品金额', '总价'];
            for (const el of document.querySelectorAll('*')) {
                const txt = (el.innerText || '').trim();
                if (!txt || txt.length > 150) continue;
                for (const kw of keywords) {
                    if (!txt.includes(kw)) continue;
                    const m = txt.match(/[¥￥]([\d,]+\.?\d*)/);
                    if (m) {
                        const v = parseFloat(m[1].replace(/,/g, ''));
                        if (v > 0) return v;
                    }
                }
            }
            return 0;
        }""")
        return float(result) if result else 0.0
    except Exception:
        return 0.0


def _click_checkout_button(cart_page) -> bool:
    """用真实鼠标点击结算按钮。"""
    try:
        coord = cart_page.evaluate("""() => {
            var keywords = ['去结算', '结算', '去下单', '立即下单'];
            var all = document.querySelectorAll('button, a, div, span');
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                var txt = (el.innerText || '').trim();
                for (var k = 0; k < keywords.length; k++) {
                    if (txt.indexOf(keywords[k]) !== -1 && txt.length < 20) {
                        var r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            return {x: r.x + r.width / 2, y: r.y + r.height / 2, txt: txt, tag: el.tagName};
                        }
                    }
                }
            }
            return null;
        }""")
        if coord:
            logger.info(f"找到结算按钮: <{coord['tag']}> \"{coord['txt']}\" ({coord['x']:.0f},{coord['y']:.0f})")
            cart_page.mouse.click(coord['x'], coord['y'])
            cart_page.wait_for_timeout(3000)
            logger.info("已点击结算按钮")
            return True
        else:
            logger.warning("未找到结算按钮")
    except Exception as e:
        logger.warning(f"点击结算按钮失败: {e}")
    return False


def _click_submit_order(page) -> bool:
    """用真实鼠标在订单确认页点击"提交订单"按钮。"""
    try:
        coord = page.evaluate("""() => {
            var keywords = ['提交订单', '确认下单', '确认订单'];
            // 用 * 搜索所有元素，包括 Web Component（如 <q-button>）
            var all = document.querySelectorAll('*');
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                var txt = (el.innerText || el.textContent || '').trim();
                for (var k = 0; k < keywords.length; k++) {
                    if (txt === keywords[k]) {
                        var r = el.getBoundingClientRect();
                        if (r.width > 30 && r.height > 15) {
                            return {x: r.x + r.width / 2, y: r.y + r.height / 2, txt: txt, tag: el.tagName};
                        }
                    }
                }
            }
            return null;
        }""")
        if coord:
            logger.info(f"找到提交订单按钮: <{coord['tag']}> \"{coord['txt']}\" ({coord['x']:.0f},{coord['y']:.0f})")
            page.mouse.click(coord['x'], coord['y'])
            page.wait_for_timeout(3000)
            logger.info("已点击提交订单按钮")
            return True
        else:
            logger.warning("未找到提交订单按钮")
    except Exception as e:
        logger.warning(f"点击提交订单按钮失败: {e}")
    return False


def _get_one_checkbox_coord(cart_page, tbody_index: int):
    """
    获取第 tbody_index 个商品 TBODY 的 checkbox 坐标。
    分两步：先 scrollIntoView，等待滚动完成，再读取坐标。
    """
    # 第一步：找到目标 TBODY 并滚动到视口中央
    found = cart_page.evaluate("""(tbIdx) => {
        var tbodies = document.querySelectorAll('tbody');
        var itemIdx = 0;
        for (var i = 0; i < tbodies.length; i++) {
            var tb = tbodies[i];
            var r = tb.getBoundingClientRect();
            if (r.width < 400 || r.height < 50) continue;
            var txt = (tb.innerText || '').trim();
            if (txt.length < 10) continue;
            var nums = txt.match(/\\d+\\.\\d{2}/g);
            if (!nums || nums.length === 0) continue;
            if (itemIdx === tbIdx) {
                tb.scrollIntoView({block: 'center'});
                return true;
            }
            itemIdx++;
        }
        return false;
    }""", tbody_index)

    if not found:
        return None

    # 等待滚动完成：反复检测目标 TBODY 的 top 坐标是否稳定
    for _ in range(10):
        cart_page.wait_for_timeout(200)
        stable = cart_page.evaluate("""(tbIdx) => {
            var tbodies = document.querySelectorAll('tbody');
            var itemIdx = 0;
            for (var i = 0; i < tbodies.length; i++) {
                var tb = tbodies[i];
                var r = tb.getBoundingClientRect();
                if (r.width < 400 || r.height < 50) continue;
                var txt = (tb.innerText || '').trim();
                if (txt.length < 10) continue;
                var nums = txt.match(/\\d+\\.\\d{2}/g);
                if (!nums || nums.length === 0) continue;
                if (itemIdx === tbIdx) {
                    // 元素在视口内即视为滚动完成
                    return r.top >= 0 && r.top < window.innerHeight;
                }
                itemIdx++;
            }
            return false;
        }""", tbody_index)
        if stable:
            break
    cart_page.wait_for_timeout(300)

    # 第二步：读取 checkbox 坐标（滚动已完成，坐标准确）
    return cart_page.evaluate("""(tbIdx) => {
        var tbodies = document.querySelectorAll('tbody');
        var itemIdx = 0;
        for (var i = 0; i < tbodies.length; i++) {
            var tb = tbodies[i];
            var r = tb.getBoundingClientRect();
            if (r.width < 400 || r.height < 50) continue;
            var txt = (tb.innerText || '').trim();
            if (txt.length < 10) continue;
            var nums = txt.match(/\\d+\\.\\d{2}/g);
            if (!nums || nums.length === 0) continue;
            if (itemIdx === tbIdx) {
                var cb = tb.querySelector('input[type="checkbox"]');
                if (cb) {
                    var cr = cb.getBoundingClientRect();
                    if (cr.width > 0 && cr.height > 0) {
                        return {x: cr.x + cr.width / 2, y: cr.y + cr.height / 2, checked: cb.checked};
                    }
                    var label = cb.closest('label') || cb.parentElement;
                    if (label) {
                        var lr = label.getBoundingClientRect();
                        if (lr.width > 0 && lr.height > 0) {
                            return {x: lr.x + lr.width / 2, y: lr.y + lr.height / 2, checked: cb.checked};
                        }
                    }
                }
                return null;
            }
            itemIdx++;
        }
        return null;
    }""", tbody_index)


def _select_group_by_mouse(cart_page, group_indices: list):
    """
    用真实鼠标逐个点击勾选指定的商品。
    group_indices: 要勾选的商品索引列表（对应 _read_cart_items 返回的 index）。
    每次点击前独立定位目标 TBODY 并 scrollIntoView，确保坐标准确。
    """
    clicked = 0
    for idx in group_indices:
        # 每次独立查询坐标（因为前一次点击可能导致 DOM 变化）
        cart_page.wait_for_timeout(300)
        coord = _get_one_checkbox_coord(cart_page, idx)
        if not coord:
            logger.warning(f"  商品[{idx}] 未找到 checkbox，跳过")
            continue
        if coord.get('checked'):
            logger.info(f"  商品[{idx}] 已勾选，跳过")
            clicked += 1
            continue

        # 真实鼠标点击
        cart_page.mouse.click(coord['x'], coord['y'])
        cart_page.wait_for_timeout(2000)  # 等待服务器响应
        clicked += 1
        logger.info(f"  鼠标勾选商品[{idx}] ({coord['x']:.0f},{coord['y']:.0f})")

    logger.info(f"已鼠标勾选 {clicked}/{len(group_indices)} 个商品")
    return clicked


def _read_item_prices(cart_page) -> list:
    """读取采购车中所有商品的索引和小计价格。"""
    return cart_page.evaluate("""() => {
        var tbodies = document.querySelectorAll('tbody');
        var results = [];
        var itemIdx = 0;
        for (var i = 0; i < tbodies.length; i++) {
            var tb = tbodies[i];
            var r = tb.getBoundingClientRect();
            if (r.width < 400 || r.height < 50) continue;
            var txt = (tb.innerText || '').trim();
            if (txt.length < 10) continue;
            var nums = txt.match(/\\d+\\.\\d{2}/g);
            if (!nums || nums.length === 0) continue;
            var price = parseFloat(nums[nums.length - 1]);
            results.push({index: itemIdx, price: price});
            itemIdx++;
        }
        return results;
    }""") or []


def _adjust_group_to_fit(cart_page, group_indices: list, all_items: list, order_limit: float) -> float:
    """
    当勾选后实际金额超过 order_limit 时，智能替换商品使金额不超限且尽量接近限额。

    算法：
    1. 计算超出金额 excess = real_amount - order_limit
    2. 在已勾选商品中，找价格 ≥ excess 且最小的商品取消（浪费最少的替换）
    3. 取消后看剩余空间 gap，从未参与本组的剩余商品中，找价格 ≤ gap 且最大的补进来
    4. 验证收银台金额，仍超限则重复，被踢出过的商品不再参与本组
    5. 最多调整 10 轮，避免意外循环

    返回调整后的实际金额，失败返回 -1。
    """
    # 当前已勾选的商品索引集合
    selected = set(group_indices)
    # 本组中被踢出过的商品（不再参与本组计算）
    excluded = set()
    # 所有商品索引集合
    all_indices = set(it['index'] for it in all_items)
    # 价格查找表
    price_map = {it['index']: it['price'] for it in all_items}

    for round_num in range(10):
        real_amount = _read_bottom_bar_amount(cart_page)
        if real_amount <= 0:
            logger.warning("无法读取收银台金额")
            return -1.0

        if real_amount <= order_limit:
            logger.info(f"  调整完成（第{round_num}轮）: ¥{real_amount:.2f} ≤ ¥{order_limit}")
            return real_amount

        excess = real_amount - order_limit
        logger.info(f"  第{round_num+1}轮调整: 实际 ¥{real_amount:.2f}，超出 ¥{excess:.2f}")

        # 在已勾选中，找价格 ≥ excess 且最小的（取消它刚好降到限额内，浪费最少）
        candidates_to_remove = [
            idx for idx in selected
            if price_map.get(idx, 0) >= excess
        ]
        if candidates_to_remove:
            # 选价格最小的（取消后浪费最少）
            to_remove = min(candidates_to_remove, key=lambda idx: price_map.get(idx, 0))
        else:
            # 没有单个商品能覆盖超出金额，取消最贵的
            to_remove = max(selected, key=lambda idx: price_map.get(idx, 0))

        # 取消勾选
        logger.info(f"  取消商品[{to_remove}] (¥{price_map.get(to_remove, 0):.2f})")
        coord = _get_one_checkbox_coord(cart_page, to_remove)
        if coord:
            cart_page.mouse.click(coord['x'], coord['y'])
            cart_page.wait_for_timeout(2000)
        selected.discard(to_remove)
        excluded.add(to_remove)

        # 读取取消后的金额
        after_remove = _read_bottom_bar_amount(cart_page)
        if after_remove <= 0:
            continue
        logger.info(f"  取消后: ¥{after_remove:.2f}")

        if after_remove > order_limit:
            # 还是超限，继续下一轮
            continue

        # 有剩余空间，尝试补入一个商品
        gap = order_limit - after_remove
        # 候选：不在已勾选中、不在本组已排除中的商品，且价格 ≤ gap
        candidates_to_add = [
            idx for idx in all_indices
            if idx not in selected and idx not in excluded
            and price_map.get(idx, 0) <= gap and price_map.get(idx, 0) > 0
        ]

        if candidates_to_add:
            # 选价格最大的（最接近填满空间）
            to_add = max(candidates_to_add, key=lambda idx: price_map.get(idx, 0))
            logger.info(f"  补入商品[{to_add}] (¥{price_map.get(to_add, 0):.2f})，剩余空间 ¥{gap:.2f}")
            coord_add = _get_one_checkbox_coord(cart_page, to_add)
            if coord_add and not coord_add.get('checked', False):
                cart_page.mouse.click(coord_add['x'], coord_add['y'])
                cart_page.wait_for_timeout(2000)
                selected.add(to_add)

                # 验证补入后是否仍在限额内
                after_add = _read_bottom_bar_amount(cart_page)
                logger.info(f"  补入后: ¥{after_add:.2f}")
                if after_add > order_limit:
                    # 补入后又超了（运费变化），取消刚补入的
                    logger.info(f"  补入后超限，取消商品[{to_add}]")
                    coord_add2 = _get_one_checkbox_coord(cart_page, to_add)
                    if coord_add2:
                        cart_page.mouse.click(coord_add2['x'], coord_add2['y'])
                        cart_page.wait_for_timeout(2000)
                    selected.discard(to_add)
                    excluded.add(to_add)
        else:
            logger.info(f"  无合适商品可补入（空间 ¥{gap:.2f}）")

    # 最终检查
    final = _read_bottom_bar_amount(cart_page)
    if final > 0 and final <= order_limit:
        return final
    logger.warning(f"调整 10 轮后金额 ¥{final:.2f}，仍不满足限额")
    return final if final > 0 and final <= order_limit else -1.0


def run_cart_checkout(context, order_limit: float = 500.0, shipping_reserve: float = 15.0):
    """
    采购车结算：预读价格 → 贪心分组 → 每组鼠标勾选 → 结算 → 提交订单。

    通过已保存的采购车 URL 在新标签页中打开采购车，不影响其他页面。

    流程：
    1. 通过已保存的采购车 URL 打开新标签页
    2. 读取所有商品小计价格，贪心分组（每组 ≤ order_limit，尽量接近）
    3. 对每组：取消全选 → 鼠标逐个勾选本组商品 → 读取收银台确认 → 结算 → 提交
    4. 返回采购车处理下一组，直到全部结算

    返回已成功结算的订单数。
    """
    logger.info("=" * 60)
    logger.info(f"开始采购车结算 | 每单限额: ¥{order_limit}")
    logger.info("=" * 60)

    # 1. 打开采购车
    cart_page = _open_cart_in_new_tab(context)
    if not cart_page:
        logger.error("无法打开采购车页面")
        return 0
    logger.info(f"采购车页面: {cart_page.url}")

    # 2. 读取所有商品及小计价格
    items = _read_cart_items(cart_page)
    if not items:
        logger.warning("采购车中未读取到商品")
        return 0

    total_amount = sum(it['price'] for it in items)
    logger.info(f"采购车共 {len(items)} 件商品，总金额 ¥{total_amount:.2f}:")
    for it in items:
        logger.info(f"  [{it['index']}] {it['name']} | ¥{it['price']:.2f}")

    # 3. 贪心分组
    groups = _group_items_for_checkout(items, limit=order_limit, shipping_reserve=shipping_reserve)
    logger.info(f"分为 {len(groups)} 笔订单:")
    for i, group in enumerate(groups):
        gtotal = sum(it['price'] for it in group)
        names = [it['name'][:15] for it in group]
        logger.info(f"  订单{i+1}: {len(group)} 件 ¥{gtotal:.2f} | {names}")
    print(f"\n  采购车共 {len(items)} 件商品 ¥{total_amount:.2f}，分为 {len(groups)} 笔订单")
    for i, group in enumerate(groups):
        gtotal = sum(it['price'] for it in group)
        print(f"    订单{i+1}: {len(group)} 件，预计 ¥{gtotal:.2f}")

    # 4. 逐组结算
    order_count = 0
    for i, group in enumerate(groups):
        gtotal = sum(it['price'] for it in group)
        group_indices = [it['index'] for it in group]
        logger.info(f"--- 订单 {i+1}/{len(groups)} | {len(group)} 件 | 预计 ¥{gtotal:.2f} ---")
        print(f"\n  结算订单 {i+1}/{len(groups)}: {len(group)} 件，预计 ¥{gtotal:.2f}")

        # 确保在采购车页面（第一组已经在了，后续组需要重新打开）
        if i > 0:
            new_cart = _open_cart_in_new_tab(context)
            if not new_cart:
                logger.warning("返回采购车失败，停止结算")
                break
            if cart_page and cart_page != new_cart:
                try:
                    cart_page.close()
                except Exception:
                    pass
            cart_page = new_cart

            # 重新读取商品列表（已结算的商品会消失，索引会变）
            items = _read_cart_items(cart_page)
            if not items:
                logger.info("采购车已无剩余商品")
                break
            # 重新分组剩余商品
            remaining_groups = _group_items_for_checkout(items, limit=order_limit)
            if not remaining_groups:
                break
            group = remaining_groups[0]
            gtotal = sum(it['price'] for it in group)
            group_indices = [it['index'] for it in group]
            logger.info(f"重新分组后，本次: {len(group)} 件 ¥{gtotal:.2f}")

        # 取消全选
        _uncheck_all(cart_page)
        random_delay(0.5, 1.0)

        # 鼠标逐个勾选本组商品
        _select_group_by_mouse(cart_page, group_indices)
        random_delay(0.5, 1.0)

        # 读取收银台实际金额，如果超限则调整
        real_amount = _read_bottom_bar_amount(cart_page)
        if real_amount > 0:
            logger.info(f"收银台实际金额: ¥{real_amount:.2f} (预计: ¥{gtotal:.2f})")
            print(f"    收银台金额: ¥{real_amount:.2f}")

            if real_amount > order_limit:
                logger.info(f"实际金额 ¥{real_amount:.2f} 超过限额 ¥{order_limit}，开始调整...")
                real_amount = _adjust_group_to_fit(cart_page, group_indices, items, order_limit)
                if real_amount <= 0:
                    logger.warning("调整后仍无法满足限额，跳过此组")
                    continue
                print(f"    调整后金额: ¥{real_amount:.2f}")
        else:
            logger.warning("无法读取收银台金额，继续结算")

        # 点击结算
        if not _click_checkout_button(cart_page):
            logger.warning(f"订单 {i+1} 结算按钮点击失败")
            save_screenshot(cart_page, f"checkout_fail_{i+1}")
            continue

        # 等待跳转到地址确认页
        try:
            cart_page.wait_for_url(
                lambda url: "order" in url or "confirm" in url or "buy" in url,
                timeout=10000
            )
            cart_page.wait_for_timeout(2000)
            logger.info(f"订单 {i+1} 已跳转至确认页: {cart_page.url}")
            print(f"    已跳转到地址确认页，正在提交...")
        except Exception:
            logger.warning(f"订单 {i+1} 未检测到页面跳转")
            save_screenshot(cart_page, f"checkout_no_redirect_{i+1}")
            _confirm_popup(cart_page)

        # 提交订单
        if _click_submit_order(cart_page):
            try:
                confirm_url = cart_page.url
                cart_page.wait_for_url(
                    lambda url: url != confirm_url,
                    timeout=15000
                )
                cart_page.wait_for_timeout(2000)
                logger.info(f"订单 {i+1} 提交成功: {cart_page.url}")
                order_count += 1
                print(f"    订单 {i+1} 提交成功！")
            except Exception:
                logger.warning(f"订单 {i+1} 提交后未检测到跳转，可能已成功")
                save_screenshot(cart_page, f"submit_no_redirect_{i+1}")
                order_count += 1
        else:
            logger.warning(f"订单 {i+1} 提交订单按钮点击失败")
            save_screenshot(cart_page, f"submit_fail_{i+1}")

        random_delay(1.0, 2.0)

    # 关闭采购车标签页
    if cart_page:
        try:
            cart_page.close()
        except Exception:
            pass

    logger.info("=" * 60)
    logger.info(f"结算完成！共生成 {order_count} 笔订单")
    logger.info("=" * 60)
    print(f"\n  结算完成！共生成 {order_count} 笔订单\n")
    return order_count
