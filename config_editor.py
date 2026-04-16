"""
配置编辑器 — 交互式修改 config.json 中的参数。
"""
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# 配置项定义：(json路径, 中文名, 类型, 说明)
CONFIG_ITEMS = [
    ("search.image_path",       "搜索图片路径",   "str",   "用于以图搜图的商品图片文件路径"),
    ("search.target_shop_name", "目标店铺名称",   "str",   "要进入的店铺名称（支持部分匹配）"),
    ("cart.target_amount",      "采购目标金额",   "float", "采购车总金额目标（元）"),
    ("cart.max_items",          "最大加购数量",   "int",   "最多加入多少件商品"),
    ("cart.order_limit",        "每单金额上限",   "float", "每笔订单不超过此金额（元）"),
    ("cart.shipping_reserve",   "运费预留金额",   "float", "每单为运费预留的空间（元）"),
    ("cart.amount_strategy",    "金额策略",       "str",   "not_exceed=不超目标 / exceed_ok=可超目标"),
    ("timeouts.login_wait",     "登录等待时间",   "int",   "等待手动登录的超时时间（毫秒）"),
    ("timeouts.element_wait",   "元素等待时间",   "int",   "页面元素出现的超时时间（毫秒）"),
    ("browser.slow_mo",         "操作延迟",       "int",   "每个浏览器操作的延迟（毫秒）"),
    ("logging.level",           "日志级别",       "str",   "DEBUG / INFO / WARNING / ERROR"),
]


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print("\n  配置已保存！\n")


def get_value(config, path):
    keys = path.split(".")
    val = config
    for k in keys:
        val = val.get(k)
        if val is None:
            return None
    return val


def set_value(config, path, value):
    keys = path.split(".")
    obj = config
    for k in keys[:-1]:
        obj = obj.setdefault(k, {})
    obj[keys[-1]] = value


def show_config(config):
    print(f"\n{'='*60}")
    print("  当前配置")
    print(f"{'='*60}")
    for i, (path, name, vtype, desc) in enumerate(CONFIG_ITEMS):
        val = get_value(config, path)
        print(f"  {i+1:2d}. {name}: {val}")
    print(f"{'='*60}\n")


def edit_item(config, idx):
    path, name, vtype, desc = CONFIG_ITEMS[idx]
    current = get_value(config, path)
    print(f"\n  {name}")
    print(f"  说明: {desc}")
    print(f"  当前值: {current}")

    new_val = input(f"  输入新值（直接回车保持不变）: ").strip()
    if not new_val:
        print("  未修改")
        return False

    try:
        if vtype == "int":
            new_val = int(new_val)
        elif vtype == "float":
            new_val = float(new_val)
        # str 类型直接使用输入值
    except ValueError:
        print(f"  输入格式错误，需要 {vtype} 类型")
        return False

    set_value(config, path, new_val)
    print(f"  已修改: {name} = {new_val}")
    return True


def main():
    if not os.path.isfile(CONFIG_PATH):
        print(f"配置文件不存在: {CONFIG_PATH}")
        return

    config = load_config()
    modified = False

    while True:
        show_config(config)
        print("  输入序号修改对应配置，输入 s 保存，输入 q 退出")
        choice = input("  请选择: ").strip().lower()

        if choice == 'q':
            if modified:
                save_choice = input("  有未保存的修改，是否保存？(y/n): ").strip().lower()
                if save_choice in ('y', 'yes'):
                    save_config(config)
            print("  再见！")
            break
        elif choice == 's':
            save_config(config)
            modified = False
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(CONFIG_ITEMS):
                if edit_item(config, idx):
                    modified = True
            else:
                print(f"  无效序号，请输入 1-{len(CONFIG_ITEMS)}")
        else:
            print("  无效输入")


if __name__ == "__main__":
    main()
