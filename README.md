# 1688 自动采购程序

基于 Playwright 的 1688 自动采购工具，支持以图搜图、自动加购、智能分组结算。

## 功能

- **以图搜图** — 上传商品图片，在 1688 搜索结果中定位目标店铺
- **自动加购** — 进入店铺全店商品列表，逐页遍历，自动选规格、调数量、加入采购车
- **金额追踪** — 本地累加商品价格，定期打开采购车校准实际金额
- **智能分组结算** — 贪心算法将商品分组（每单不超过限额，预留运费），逐组勾选结算
- **真实鼠标操作** — 所有关键操作（勾选、结算、提交）使用真实鼠标点击，触发框架事件
- **反检测** — Playwright Stealth + 自定义脚本，隐藏自动化标记

## 环境要求

- Python 3.8+
- Google Chrome（推荐）或 Chromium
- Linux（已在 Ubuntu 22.04 上测试）

## 安装

```bash
git clone https://github.com/DaimaoHandle/1688-auto-purchase.git
cd 1688-auto-purchase
```

首次运行时程序会自动检查并安装依赖（Playwright、浏览器内核等），也可手动安装：

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## 配置

编辑 `config.json`：

```json
{
  "search": {
    "image_path": "商品图片路径",
    "target_shop_name": "目标店铺名称"
  },
  "cart": {
    "target_amount": 2000,
    "max_items": 200,
    "order_limit": 500,
    "shipping_reserve": 15
  }
}
```

| 配置项 | 说明 |
|--------|------|
| `image_path` | 用于以图搜图的商品图片路径 |
| `target_shop_name` | 目标店铺名称（支持部分匹配） |
| `target_amount` | 采购车目标总金额（元） |
| `max_items` | 最大加购商品数 |
| `order_limit` | 每笔订单金额上限（元） |
| `shipping_reserve` | 每单预留运费空间（元） |

## 使用

```bash
python3 main.py
```

程序流程：

1. 启动浏览器，等待手动登录 1688
2. 上传图片搜索，定位目标店铺，进入全店商品列表
3. 逐页遍历商品，自动加入采购车，达到目标金额后停止
4. 提示是否进行结算（可先在浏览器中检查采购车）
5. 确认后自动分组结算：勾选商品 → 点击结算 → 提交订单

## 项目结构

```
main.py              # 主程序入口
config.json          # 配置文件
requirements.txt     # Python 依赖
src/
  browser.py         # 浏览器初始化与反检测
  login.py           # 登录等待与验证检测
  search.py          # 以图搜图
  shop.py            # 店铺定位、商品列表、翻页
  cart.py            # 加购、金额校准、分组结算
  utils.py           # 工具函数
  setup_env.py       # 环境检查与依赖安装
```

## 注意事项

- 登录和验证码需要手动完成，程序会等待
- 首次运行会提示是否安装依赖，选择"否"可跳过（需确保依赖已安装）
- 浏览器 Profile 保存在 `~/1688/browser_profile/`，保留登录状态
- 日志输出到 `logs/app.log`

## License

MIT
