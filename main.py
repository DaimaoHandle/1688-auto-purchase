import sys
import os

# 确保从项目根目录运行
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils import load_config, setup_logging
from src.browser import init_browser
from src.login import wait_for_login
from src.search import image_search
from src.shop import find_shop_and_enter
from src.cart import run_cart_filling, run_cart_checkout, capture_cart_url, set_cart_url
from src.report import save_report
from src.selector_health import get_tracker


def main():
    config = load_config("config.json")
    logger = setup_logging(config)

    logger.info("=" * 60)
    logger.info("1688 自动采购程序启动")
    logger.info("=" * 60)

    playwright = browser = context = page = None
    added = 0
    orders = 0
    errors = []

    try:
        # 初始化浏览器
        playwright, browser, context, page = init_browser(config)
        logger.info("浏览器已启动")

        # 等待用户登录
        login_timeout = config.get("timeouts", {}).get("login_wait", 120000)
        wait_for_login(page, timeout_ms=login_timeout)

        # 登录后从首页获取采购车 URL（后续所有采购车操作都用此 URL 打开新标签）
        cart_url = capture_cart_url(page)
        if cart_url:
            set_cart_url(cart_url)
        else:
            logger.warning("未能获取采购车URL，结算功能可能不可用")
            errors.append("未能获取采购车URL")

        # 搜图
        image_path = config["search"]["image_path"]
        result_page = image_search(context, page, image_path)

        # 找到目标店铺并进入全店商品列表
        shop_name = config["search"]["target_shop_name"]
        shop_page = find_shop_and_enter(context, result_page, shop_name)

        # 填充采购车
        added = run_cart_filling(context, shop_page, config["cart"])

        logger.info(f"加购完成，共加入 {added} 件商品")
        print(f"\n>>> 加购完成，共 {added} 件商品 <<<")
        print(f"  请在浏览器中检查采购车，确认无误后进行结算。\n")

        # 询问用户是否进行结算
        while True:
            choice = input("  是否进行采购车结算？(y/n): ").strip().lower()
            if choice in ("y", "yes", "是"):
                break
            elif choice in ("n", "no", "否"):
                logger.info("用户选择不结算，程序退出")
                print("\n>>> 已取消结算，程序退出 <<<\n")
                save_report(config, added, 0, errors)
                return
            else:
                print("  请输入 y 或 n")

        # 结算：分组下单，每单不超过限额（预留运费空间）
        order_limit = config.get("cart", {}).get("order_limit", 500)
        shipping_reserve = config.get("cart", {}).get("shipping_reserve", 15)
        orders = run_cart_checkout(context, order_limit=order_limit, shipping_reserve=shipping_reserve)

        logger.info("=" * 60)
        logger.info(f"任务完成！共加入 {added} 件商品，生成 {orders} 笔订单")
        logger.info("=" * 60)

        # 保持运行，等待用户手动退出
        input("按 Enter 键退出程序...")

    except KeyboardInterrupt:
        logger.info("用户中断，程序退出")
    except Exception as e:
        logger.error(f"程序异常退出: {e}", exc_info=True)
        errors.append(str(e))
        if page:
            from src.utils import save_screenshot
            path = save_screenshot(page, "fatal_error")
            if path:
                logger.error(f"错误截图已保存: {path}")
    finally:
        # 生成运行报告
        try:
            save_report(config, added, orders, errors)
        except Exception:
            pass

        # 生成选择器健康报告
        try:
            tracker = get_tracker()
            broken = tracker.get_broken_groups()
            if broken or tracker.get_summary():
                tracker.save_report()
        except Exception:
            pass

        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if playwright:
            try:
                playwright.stop()
            except Exception:
                pass


if __name__ == "__main__":
    main()
