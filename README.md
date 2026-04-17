# 1688 自动采购系统

基于 Playwright 的 1688 自动采购工具，支持多服务器统一管理、以图搜图、自动加购、智能分组结算。

## 系统架构

```
                    ┌──────────────────────┐
                    │   Web 管理服务器      │
                    │   FastAPI + SQLite    │
                    │   Vue 3 + Element Plus│
                    └──────────┬───────────┘
                               │ WebSocket
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
     ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
     │ 采购服务器 A  │ │ 采购服务器 B  │ │ 采购服务器 C  │
     │ Agent + 浏览器│ │ Agent + 浏览器│ │ Agent + 浏览器│
     └──────────────┘ └──────────────┘ └──────────────┘
```

## 核心功能

### 采购自动化
- **以图搜图** — 上传商品图片，在 1688 搜索结果中定位目标店铺
- **自动联系客服** — 进入详情页后自动给客服发送消息
- **正常采购模式** — 按销量排序，优先采购无销量商品
- **新品采购模式** — 优先采购当日上新商品，不足再补全部商品
- **智能分组结算** — 贪心算法分组（每单不超限额，预留运费），超限自动调整
- **采购去重** — 基于 offerId 全局去重，所有采购端共享，商品列表页直接过滤
- **真实鼠标操作** — 所有关键操作使用真实鼠标点击，绕过框架事件检测
- **反检测** — Playwright Stealth + 自定义脚本

### 多服务器管理
- **Web 管理面板** — Vue 3 + Element Plus，现代化后台界面
- **节点管理** — 卡片式管理，显示在线状态、服务器 IP、任务进度
- **远程控制** — 启动/停止任务、确认结算、代码更新
- **远程配置** — 在线编辑采购配置、上传搜索图片、选择店铺
- **实时监控** — WebSocket 推送任务状态、进度条、日志流
- **数据中心** — 采购记录表格、统计卡片、CSV 导出

### 系统管理
- **账号系统** — 登录认证、用户管理（创建/编辑/禁用）
- **操作审计** — 记录谁在什么时间对哪个节点做了什么操作
- **运行报告** — 每次任务生成 JSON 报告（商品数、订单数、金额、异常）
- **选择器健康检测** — 追踪页面选择器命中率，1688 改版时自动告警
- **错误重试与自愈** — 熔断器、页面刷新、验证码检测

## 环境要求

### 采购服务器
- Python 3.8+
- Google Chrome 或 Chromium
- Linux（Ubuntu 22.04 已测试）
- 有图形界面（Playwright 需要可见浏览器）

### 管理服务器
- Python 3.10+
- Linux（无需图形界面）

## 快速开始

### 1. 管理服务器部署

```bash
git clone https://github.com/DaimaoHandle/1688-auto-purchase.git
cd 1688-auto-purchase
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-server.txt
nohup python3 server/server_main.py > /tmp/server.log 2>&1 &
```

访问 `http://服务器IP:1688`，默认账号 `admin` / `admin`。

### 2. 采购服务器部署

```bash
git clone https://github.com/DaimaoHandle/1688-auto-purchase.git
cd 1688-auto-purchase
python3 bootstrap.py
pip install -r requirements-agent.txt
```

在管理面板添加节点，获取 `node_id` 和 `token`，填入 `agent/agent_config.json`：

```json
{
  "server_url": "ws://管理服务器IP:1688/ws/agent",
  "node_id": "从管理面板获取",
  "token": "从管理面板获取"
}
```

启动 Agent：

```bash
nohup python3 agent/agent_main.py > /tmp/agent.log 2>&1 &
```

### 3. 独立模式（不用管理面板）

```bash
python3 bootstrap.py
python3 config_editor.py  # 编辑配置
python3 main.py           # 运行采购
```

## 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `search.image_path` | 搜索图片路径 | - |
| `search.target_shop_name` | 目标店铺名称 | - |
| `cart.target_amount` | 采购目标金额（元） | 2000 |
| `cart.purchase_mode` | 采购模式：`normal` / `new_product` | normal |
| `cart.order_limit` | 每笔订单金额上限（元） | 500 |
| `cart.shipping_reserve` | 每单预留运费（元） | 15 |
| `cart.max_items` | 最大加购商品数 | 200 |

## 项目结构

```
├── main.py                  # 独立模式入口
├── bootstrap.py             # 环境准备
├── config.json              # 本地配置
├── config_editor.py         # 交互式配置编辑器
│
├── src/                     # 采购核心逻辑
│   ├── browser.py           # 浏览器初始化与反检测
│   ├── login.py             # 登录等待与验证检测
│   ├── search.py            # 以图搜图
│   ├── shop.py              # 店铺定位、新品专区、翻页、客服消息
│   ├── cart.py              # 加购、销量排序、去重、分组结算
│   ├── purchase_history.py  # 采购去重（管理端API共享）
│   ├── retry.py             # 错误重试与熔断器
│   ├── selector_health.py   # 选择器健康检测
│   ├── report.py            # 运行报告
│   └── utils.py             # 工具函数
│
├── agent/                   # 采购端 Agent
│   ├── agent_main.py        # Agent 入口
│   ├── ws_client.py         # WebSocket 客户端（自动重连）
│   ├── worker.py            # Playwright 工作线程
│   └── log_interceptor.py   # 日志转发
│
├── server/                  # 管理端服务器
│   ├── server_main.py       # 服务器入口
│   ├── app.py               # FastAPI 应用
│   ├── api/                 # REST API
│   │   ├── auth.py          # 认证
│   │   ├── users.py         # 用户管理
│   │   ├── nodes.py         # 节点管理
│   │   ├── tasks.py         # 任务控制
│   │   ├── configs.py       # 节点配置
│   │   ├── images.py        # 图片管理
│   │   ├── reports.py       # 报告汇总
│   │   ├── purchased.py     # 采购历史（去重）
│   │   └── audit.py         # 操作审计
│   ├── ws/                  # WebSocket
│   │   └── agent_handler.py # Agent 连接处理
│   ├── db/                  # 数据库
│   │   └── database.py      # SQLite + 自动迁移
│   ├── services/            # 服务层
│   │   └── node_manager.py  # 节点状态管理
│   └── static/              # 前端
│       └── index.html       # Vue 3 + Element Plus SPA
│
├── shared/                  # 共享协议
│   └── protocol.py          # WebSocket 消息定义
│
├── requirements.txt         # 采购端依赖
├── requirements-agent.txt   # Agent 依赖
├── requirements-server.txt  # 服务器依赖
└── Dockerfile               # Docker 部署
```

## 管理面板功能

| 菜单 | 功能 |
|------|------|
| 总览 | 统计卡片（节点/在线/运行/今日成功/失败/采购额）+ 节点状态概览 |
| 节点管理 | 节点卡片（详情/配置/启动/停止/更新代码/日志）|
| 任务中心 | 开发中 |
| 数据中心 | 采购记录表格、统计、CSV 导出 |
| 系统设置 | 用户管理、操作日志 |

## 通信协议

采购服务器（仅出站）通过 WebSocket 主动连接管理服务器，支持：
- 注册认证（node_id + token）
- 心跳保活（15秒）
- 远程启停任务
- 实时状态/进度/日志推送
- 结算审批
- 代码更新指令

## License

MIT
