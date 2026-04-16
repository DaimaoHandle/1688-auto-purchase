import os
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth


# 注入脚本：覆盖所有常见的自动化检测点
_STEALTH_INIT_SCRIPT = """
() => {
    // 隐藏 webdriver 标记
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true,
    });

    // 伪造 plugins（真实 Chrome 有插件列表）
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const arr = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
            ];
            arr.__proto__ = PluginArray.prototype;
            return arr;
        },
        configurable: true,
    });

    // 伪造 languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['zh-CN', 'zh', 'en'],
        configurable: true,
    });

    // 隐藏 CDP / Playwright 全局变量
    const toDelete = [
        '__playwright', '__pw_manual', '__PW_inspect',
        'playwright', '_playwrightConsoleMessages',
        '__cdc_adoQpoasnfa76pfcZLmcfl_Symbol',
        '__cdc_adoQpoasnfa76pfcZLmcfl_Promise',
        '__cdc_adoQpoasnfa76pfcZLmcfl_Array',
    ];
    toDelete.forEach(k => {
        try { delete window[k]; } catch(e) {}
    });

    // 隐藏 chrome.runtime 异常行为
    if (!window.chrome) {
        window.chrome = {};
    }
    if (!window.chrome.runtime) {
        window.chrome.runtime = { id: undefined };
    }

    // 覆盖 Permissions API（避免返回 'denied' 暴露自动化）
    if (navigator.permissions && navigator.permissions.query) {
        const origQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = (params) => {
            if (params && params.name === 'notifications') {
                return Promise.resolve({ state: 'denied', onchange: null });
            }
            return origQuery(params);
        };
    }
}
"""


def init_browser(config: dict):
    browser_cfg = config.get("browser", {})
    playwright = sync_playwright().start()

    # 持久化 Profile 目录：保留 cookies、localStorage、指纹缓存
    profile_dir = os.path.expanduser(browser_cfg.get("profile_dir", "~/1688/browser_profile"))
    os.makedirs(profile_dir, exist_ok=True)

    # 启动参数：关闭自动化特征标记
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions-except=",
        "--disable-default-apps",
    ]

    # 使用系统安装的真实 Chrome；若不存在则回退 Chromium
    chrome_path = "/usr/bin/google-chrome"
    channel = None
    executable = None
    if os.path.exists(chrome_path):
        executable = chrome_path
    else:
        channel = "chromium"

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=browser_cfg.get("headless", False),
        slow_mo=browser_cfg.get("slow_mo", 100),
        executable_path=executable,
        channel=channel,
        viewport={"width": 1920, "height": 1080},
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        args=launch_args,
        ignore_default_args=["--enable-automation"],  # 去掉自动化标志
    )

    # 对所有新页面注入反检测脚本（自定义脚本）
    context.add_init_script(_STEALTH_INIT_SCRIPT)

    stealth = Stealth(
        navigator_languages_override=("zh-CN", "zh"),
        navigator_platform_override="Linux x86_64",
    )

    timeout_ms = config.get("timeouts", {}).get("element_wait", 10000)

    # 复用已有页面，或新建一个
    if context.pages:
        page = context.pages[0]
    else:
        page = context.new_page()

    page.set_default_timeout(timeout_ms)

    # 对当前页面应用 stealth
    stealth.apply_stealth_sync(page)

    # 对后续新建的页面也自动应用 stealth
    def _on_new_page(new_page):
        new_page.set_default_timeout(timeout_ms)
        stealth.apply_stealth_sync(new_page)

    context.on("page", _on_new_page)

    # browser=None：persistent context 自身管理浏览器生命周期
    return playwright, None, context, page
