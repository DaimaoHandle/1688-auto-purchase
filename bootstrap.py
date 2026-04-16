"""
环境准备脚本 — 检测并安装所有依赖，管理浏览器登录态。

与 main.py 分离，职责单一：
  - 检查 Python 版本
  - 安装 pip 依赖
  - 安装 Playwright 浏览器内核
  - 安装系统级共享库（Linux）
  - 检测 Google Chrome
  - 管理浏览器 Profile（登录态）

每次运行都会执行完整检测（已安装的会快速跳过），无需标记文件。
"""
import os
import sys
import subprocess
import importlib.util
import shutil

_ROOT = os.path.dirname(os.path.abspath(__file__))
_REQUIREMENTS = os.path.join(_ROOT, "requirements.txt")

_REQUIRED_PACKAGES = [
    ("playwright>=1.41.0", "playwright"),
    ("playwright-stealth>=2.0.0", "playwright_stealth"),
]

_PLAYWRIGHT_BROWSER = "chromium"

_CHROME_PATHS = [
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/opt/google/chrome/chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]

_PROFILE_DIR = os.path.expanduser("~/1688/browser_profile")


def _print(msg: str, level: str = "INFO"):
    icons = {"OK": "\u2713", "WARN": "!", "ERROR": "\u2717", "INFO": "\u00b7", "RUN": "\u2192"}
    icon = icons.get(level, "\u00b7")
    print(f"  [{icon}] {msg}", flush=True)


def _run_cmd(cmd: str, capture_output: bool = False):
    result = subprocess.run(
        cmd, shell=True,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.STDOUT if capture_output else None,
        text=True,
    )
    return result.returncode, (result.stdout or "") if capture_output else ""


def check_python_version():
    major, minor = sys.version_info[:2]
    if major < 3 or (major == 3 and minor < 8):
        _print(f"Python 版本过低 ({major}.{minor})，需要 3.8 或以上", "ERROR")
        sys.exit(1)
    _print(f"Python {major}.{minor}.{sys.version_info[2]}", "OK")


def install_pip_packages():
    _print("检查 Python 包依赖...", "INFO")
    missing = [
        pip_name
        for pip_name, import_name in _REQUIRED_PACKAGES
        if importlib.util.find_spec(import_name) is None
    ]
    if not missing:
        _print("所有 Python 包已安装", "OK")
        return

    _print(f"缺少: {', '.join(missing)}，开始安装...", "RUN")
    rc, _ = _run_cmd(f'"{sys.executable}" -m pip install -r "{_REQUIREMENTS}"')
    if rc != 0:
        _print("pip 安装失败，请手动执行:", "ERROR")
        _print(f"  pip install -r {_REQUIREMENTS}", "ERROR")
        sys.exit(1)

    still_missing = [
        pip_name
        for pip_name, import_name in _REQUIRED_PACKAGES
        if importlib.util.find_spec(import_name) is None
    ]
    if still_missing:
        _print(f"安装后仍缺少: {', '.join(still_missing)}", "ERROR")
        sys.exit(1)

    _print("Python 包安装完成", "OK")


def install_playwright_browser():
    _print(f"检查 Playwright {_PLAYWRIGHT_BROWSER} 内核...", "INFO")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser_exec = pw.chromium.executable_path
        if os.path.isfile(browser_exec):
            _print(f"Playwright Chromium 已存在: {browser_exec}", "OK")
            return
    except Exception:
        pass

    _print("正在下载 Playwright Chromium...", "RUN")
    rc, _ = _run_cmd(f'"{sys.executable}" -m playwright install {_PLAYWRIGHT_BROWSER}')
    if rc != 0:
        _print("下载失败，请手动执行:", "ERROR")
        _print(f"  python -m playwright install {_PLAYWRIGHT_BROWSER}", "ERROR")
        sys.exit(1)
    _print("Playwright Chromium 安装完成", "OK")


def install_system_deps():
    if not sys.platform.startswith("linux"):
        return
    _print("检查 Chromium 系统级依赖...", "INFO")
    rc, _ = _run_cmd(
        f'"{sys.executable}" -m playwright install-deps {_PLAYWRIGHT_BROWSER}',
        capture_output=True,
    )
    if rc == 0:
        _print("系统依赖已满足", "OK")
    else:
        _print("系统依赖安装失败（可能需要 sudo）", "WARN")
        _print(f"  sudo python -m playwright install-deps {_PLAYWRIGHT_BROWSER}", "WARN")


def check_google_chrome():
    for path in _CHROME_PATHS:
        if os.path.isfile(path):
            _print(f"Google Chrome: {path}", "OK")
            return
    _print("未检测到 Google Chrome，将使用 Playwright Chromium", "WARN")
    _print("如需安装 Chrome（推荐）:", "WARN")
    _print("  wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb", "WARN")
    _print("  sudo apt install ./google-chrome-stable_current_amd64.deb", "WARN")


def manage_browser_profile():
    print()
    _print("检查浏览器登录态...", "INFO")

    if not os.path.isdir(_PROFILE_DIR):
        _print("无已保存的登录态", "INFO")
        return

    # 检查 Profile 目录是否有实际内容
    file_count = sum(1 for _ in os.scandir(_PROFILE_DIR))
    if file_count == 0:
        _print("登录态目录为空", "INFO")
        return

    _print(f"检测到已保存的浏览器登录态 ({file_count} 个文件)", "INFO")
    print()
    print("  保留登录态可以跳过手动登录步骤。")
    print("  如果更换了服务器或账号，建议清除后重新登录。")
    print()

    while True:
        choice = input("  是否保留当前登录态？(y=保留 / n=清除): ").strip().lower()
        if choice in ("y", "yes"):
            _print("已保留登录态", "OK")
            return
        elif choice in ("n", "no"):
            break
        else:
            print("  请输入 y 或 n")

    try:
        shutil.rmtree(_PROFILE_DIR)
        os.makedirs(_PROFILE_DIR, exist_ok=True)
        _print("登录态已清除，下次启动程序需要重新登录", "OK")
    except Exception as e:
        _print(f"清除失败: {e}", "WARN")
        _print(f"可手动删除: {_PROFILE_DIR}", "WARN")


def main():
    print()
    print("=" * 52)
    print("  环境准备")
    print("=" * 52)
    print()

    check_python_version()
    install_pip_packages()
    install_playwright_browser()
    install_system_deps()
    check_google_chrome()
    manage_browser_profile()

    print()
    print("=" * 52)
    print("  环境准备完成！可以运行 python3 main.py 启动程序")
    print("=" * 52)
    print()


if __name__ == "__main__":
    main()
