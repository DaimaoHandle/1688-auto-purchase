"""
环境自检与依赖安装模块。

首次运行时（项目根目录下不存在 .env_ready 标记文件）自动执行：
  1. 检查 Python 版本（需 3.8+）
  2. 安装 requirements.txt 中的 Python 包
  3. 安装 Playwright 浏览器内核（Chromium）
  4. 安装 Chromium 所需的系统级共享库（Linux，需要 sudo）
  5. 检测 Google Chrome 是否存在（可选，缺失时给出提示）

后续运行时检测到标记文件直接跳过，不会重复执行。
若需重新检测，删除项目根目录下的 .env_ready 文件即可。
"""

import os
import sys
import subprocess
import importlib.util

# 项目根目录（此文件在 src/ 下，上一级为根目录）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MARKER = os.path.join(_ROOT, ".env_ready")
_REQUIREMENTS = os.path.join(_ROOT, "requirements.txt")

# 需要检测的包：(pip 包名, import 名)
_REQUIRED_PACKAGES = [
    ("playwright>=1.41.0", "playwright"),
    ("playwright-stealth>=2.0.0", "playwright_stealth"),
]

# Playwright 支持的浏览器内核
_PLAYWRIGHT_BROWSER = "chromium"

# 常见 Google Chrome 安装路径（Linux）
_CHROME_PATHS = [
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/opt/google/chrome/chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]


# ─── 工具函数 ──────────────────────────────────────────────────────────────────

def _print(msg: str, level: str = "INFO"):
    icons = {"OK": "✓", "WARN": "!", "ERROR": "✗", "INFO": "·", "RUN": "→"}
    icon = icons.get(level, "·")
    print(f"  [{icon}] {msg}", flush=True)


def _run_cmd(cmd: str, capture_output: bool = False) -> tuple[int, str]:
    """执行 shell 命令，返回 (returncode, output)。"""
    result = subprocess.run(
        cmd, shell=True,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.STDOUT if capture_output else None,
        text=True,
    )
    return result.returncode, (result.stdout or "") if capture_output else ""


# ─── 检测步骤 ──────────────────────────────────────────────────────────────────

def _check_python_version():
    major, minor = sys.version_info[:2]
    if major < 3 or (major == 3 and minor < 8):
        _print(f"Python 版本过低 ({major}.{minor})，需要 3.8 或以上", "ERROR")
        sys.exit(1)
    _print(f"Python {major}.{minor}.{sys.version_info[2]}", "OK")


def _install_pip_packages():
    _print("检查 Python 包依赖...", "INFO")

    missing = [
        pip_name
        for pip_name, import_name in _REQUIRED_PACKAGES
        if importlib.util.find_spec(import_name) is None
    ]

    if not missing:
        _print("所有 Python 包已安装", "OK")
        return

    _print(f"缺少以下包，开始安装: {', '.join(missing)}", "RUN")
    rc, _ = _run_cmd(f'"{sys.executable}" -m pip install -r "{_REQUIREMENTS}"')
    if rc != 0:
        _print("pip 安装失败，请手动执行以下命令后重试:", "ERROR")
        _print(f"  pip install -r {_REQUIREMENTS}", "ERROR")
        sys.exit(1)

    # 二次确认
    still_missing = [
        pip_name
        for pip_name, import_name in _REQUIRED_PACKAGES
        if importlib.util.find_spec(import_name) is None
    ]
    if still_missing:
        _print(f"安装后仍缺少: {', '.join(still_missing)}，请检查 pip 配置", "ERROR")
        sys.exit(1)

    _print("Python 包安装完成", "OK")


def _install_playwright_browser():
    _print(f"检查 Playwright {_PLAYWRIGHT_BROWSER} 内核...", "INFO")

    # playwright 此时已确认安装，可以安全导入
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser_exec = pw.chromium.executable_path
        if os.path.isfile(browser_exec):
            _print(f"Playwright Chromium 已存在: {browser_exec}", "OK")
            return
    except Exception:
        pass  # 可能是 playwright 安装后首次，executable_path 会抛异常

    _print(f"正在下载 Playwright Chromium（首次下载约需数分钟）...", "RUN")
    rc, _ = _run_cmd(f'"{sys.executable}" -m playwright install {_PLAYWRIGHT_BROWSER}')
    if rc != 0:
        _print("Playwright 浏览器下载失败，请手动执行:", "ERROR")
        _print(f"  python -m playwright install {_PLAYWRIGHT_BROWSER}", "ERROR")
        sys.exit(1)
    _print("Playwright Chromium 安装完成", "OK")


def _install_system_deps():
    """安装 Chromium 运行所需的系统共享库（仅 Linux，需要 sudo/root）。"""
    if not sys.platform.startswith("linux"):
        return

    _print("检查 Chromium 系统级依赖...", "INFO")

    # playwright install-deps 会自动判断缺失项，无缺失时快速退出
    rc, output = _run_cmd(
        f'"{sys.executable}" -m playwright install-deps {_PLAYWRIGHT_BROWSER}',
        capture_output=True,
    )
    if rc == 0:
        _print("系统依赖已满足", "OK")
    else:
        _print("系统依赖安装失败（通常需要 root/sudo 权限）", "WARN")
        _print("若启动浏览器时报错，请以 root 身份手动执行:", "WARN")
        _print(f"  sudo python -m playwright install-deps {_PLAYWRIGHT_BROWSER}", "WARN")


def _check_google_chrome():
    """检测真实 Google Chrome 是否存在（可选，缺失时回退到 Playwright Chromium）。"""
    for path in _CHROME_PATHS:
        if os.path.isfile(path):
            _print(f"Google Chrome: {path}", "OK")
            return
    _print(
        "未检测到 Google Chrome，将使用 Playwright 内置 Chromium（反检测能力略弱）",
        "WARN",
    )
    _print("如需安装 Chrome（推荐）:", "WARN")
    _print(
        "  wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb",
        "WARN",
    )
    _print("  sudo apt install ./google-chrome-stable_current_amd64.deb", "WARN")


def _clear_browser_profile():
    """
    清除浏览器 Profile 目录（Cookies、localStorage、登录态等）。
    在新服务器首次运行时调用，防止从其他服务器打包携带的旧登录态被沿用。
    """
    import shutil

    profile_dir = os.path.expanduser("~/1688/browser_profile")
    if not os.path.isdir(profile_dir):
        return  # 不存在则无需清理

    _print("检测到旧浏览器配置（来自其他服务器的打包），正在清除登录态...", "RUN")
    try:
        shutil.rmtree(profile_dir)
        os.makedirs(profile_dir, exist_ok=True)
        _print("浏览器配置已清除，程序启动后请重新登录 1688", "OK")
    except Exception as e:
        _print(f"清除浏览器配置失败: {e}", "WARN")
        _print(f"如遇登录问题，请手动删除该目录后重试: {profile_dir}", "WARN")


# ─── 入口 ──────────────────────────────────────────────────────────────────────

def check_and_setup():
    """
    程序启动时的环境检查入口。
    - 若已存在 .env_ready 标记文件，视为非首次运行，直接跳过。
    - 否则询问用户是否为首次运行：
        - 选"是"：执行完整环境检查与依赖安装，完成后写入标记文件。
        - 选"否"：仅写入标记文件，后续不再询问。
    """
    if os.path.isfile(_MARKER):
        return

    print()
    print("=" * 52)
    print("  请确认是否为首次运行本程序")
    print("=" * 52)
    print()
    print("  首次运行将自动检查并安装所有依赖（Python 包、浏览器内核、系统库等）。")
    print('  如果您已在此环境中运行过本程序且依赖完整，可选择"否"跳过。')
    print()

    while True:
        choice = input("  是否为首次运行？(y/n): ").strip().lower()
        if choice in ("y", "yes", "是"):
            is_first_run = True
            break
        elif choice in ("n", "no", "否"):
            is_first_run = False
            break
        else:
            print("  请输入 y 或 n")

    if is_first_run:
        print()
        print("=" * 52)
        print("  首次运行 — 开始检查并安装运行环境")
        print("=" * 52)

        _check_python_version()
        _install_pip_packages()
        _install_playwright_browser()
        _install_system_deps()
        _check_google_chrome()
        # 清除从其他服务器打包携带的旧浏览器登录态
        _clear_browser_profile()

        print("=" * 52)
        print("  环境检查通过，程序启动中...")
        print("=" * 52)
        print()
    else:
        print()
        _print("已跳过环境检查", "OK")
        print()

    # 写入标记文件，后续启动不再询问
    try:
        with open(_MARKER, "w") as f:
            f.write("1")
    except OSError as e:
        _print(f"标记文件写入失败（下次启动会重新询问）: {e}", "WARN")
