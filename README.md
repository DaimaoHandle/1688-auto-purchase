# 1688 自动采购系统

基于 Playwright 的 1688 自动采购工具，支持多服务器统一管理、以图搜图/搜店、自动加购、智能分组结算、任务暂停恢复。

## 系统架构

```
                         ┌──────────────────────────────────┐
                         │       Web 管理服务器              │
                         │   FastAPI + SQLite + Vue 3 SPA   │
  浏览器 ─── HTTPS ────► │   手机 / 平板 / 桌面 自适应      │
  (管理员)               └──────────┬───────────────────────┘
                                    │ WebSocket (采购端主动出站)
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
           ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
           │ 采购服务器 A  │ │ 采购服务器 B  │ │ 采购服务器 C  │
           │ (仅出站)     │ │ (仅出站)     │ │ (仅出站)     │
           │ Agent + 浏览器│ │ Agent + 浏览器│ │ Agent + 浏览器│
           └──────────────┘ └──────────────┘ └──────────────┘
```

**关键设计**：采购服务器安全组仅允许出站，通过 WebSocket 主动连接管理服务器，连接建立后双向通信。

## 核心功能

### 采购自动化
- **以图搜图** — 上传商品图片，在 1688 搜索结果中定位目标店铺
- **搜店模式** — 在 1688 首页搜索框直接搜索店铺名，模拟真实用户操作
- **自动联系客服** — 进入详情页后通过 iframe 自动给客服发送消息
- **正常采购模式** — 按销量排序，优先采购无销量商品
- **新品采购模式** — 优先采购当日上新商品，不足再补全部商品
- **智能分组结算** — 贪心算法分组（每单不超限额，预留运费），超限自动替换商品
- **采购去重** — 基于 offerId 全局去重，所有采购端共享历史记录
- **起批数量检测** — 结算勾选后验证 checkbox 状态，自动跳过不满足起批数量的商品
- **店铺验证** — 进入详情页后验证店铺名是否与目标匹配，不匹配自动重试
- **真实鼠标操作** — 所有关键操作使用 `page.mouse.click(x, y)`，兼容 1688 React 框架
- **反检测** — Playwright Stealth + 自定义脚本

### 任务控制
- **状态机驱动** — 可持久化、可恢复、可审计的任务执行引擎
- **细粒度暂停/恢复** — 基于 `threading.Event` 检查点，在任意 Playwright 操作间隙暂停，恢复后从断点继续
- **错误分类** — RetryableError / ManualError / FatalError 三级错误分类
- **熔断器** — 连续失败自动升级恢复策略（刷新→重导航→重启浏览器→中止）
- **验证码检测** — 多层检测（URL + DOM + JS 深度扫描 + iframe），等待人工处理

### 多服务器管理
- **Web 管理面板** — Vue 3 + Element Plus，手机/平板/桌面多端自适应
- **节点管理** — 卡片式管理，显示在线状态、IP、任务进度
- **远程控制** — 启动/停止/暂停/恢复任务、确认/拒绝结算、代码更新
- **远程配置** — 在线编辑采购配置、上传搜索图片、选择店铺
- **实时监控** — WebSocket 推送任务状态、加购进度条、日志流（事件/运行/全部三视图）
- **任务计划** — 创建多节点批量采购计划，一键执行
- **数据中心** — ECharts 趋势图、按节点统计、采购记录表格、CSV 导出
- **节点拖拽排序** — SortableJS 支持卡片拖拽排序

### 系统管理
- **账号系统** — Session 认证（持久化到 SQLite）、用户管理（创建/编辑/禁用）
- **操作审计** — 记录所有关键操作（节点增删改、任务启停暂停、配置保存、用户管理、结算审批）
- **运行报告** — 每次任务生成结构化报告（商品数、订单数、金额、状态轨迹、异常列表）
- **选择器健康检测** — 追踪页面选择器命中率，1688 改版时自动告警
- **采购车清空** — 任务启动前自动检查并清空采购车残留商品

## 环境要求

### 采购服务器
- Python 3.8+
- Google Chrome 或 Chromium
- Linux（Ubuntu 22.04 已测试）
- 有图形界面（Playwright 需要可见浏览器）
- 安全组：仅需出站

### 管理服务器
- Python 3.10+
- Linux（无需图形界面）
- 安全组：进出站开放

## 快速开始

### 1. 管理服务器部署

```bash
git clone https://github.com/DaimaoHandle/1688-auto-purchase.git
cd 1688-auto-purchase
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-server.txt
python3 server/server_main.py
```

访问 `http://服务器IP:8080`，默认账号 `admin` / `admin123456`。

推荐使用 systemd 管理：

```ini
# /etc/systemd/system/1688-server.service
[Unit]
Description=1688 Purchase Management Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/1688-auto-purchase
ExecStart=/home/ubuntu/1688-auto-purchase/venv/bin/python3 server/server_main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

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
  "server_url": "ws://管理服务器IP:8080/ws/agent",
  "node_id": "从管理面板获取",
  "token": "从管理面板获取"
}
```

启动 Agent：

```bash
nohup python3 agent/agent_main.py > /tmp/agent.log 2>&1 &
```

或使用 systemd：

```ini
# /etc/systemd/system/1688-agent.service
[Unit]
Description=1688 Purchase Agent
After=network.target

[Service]
Type=simple
User=admin
Environment=DISPLAY=:1
WorkingDirectory=/home/admin/1688-auto-purchase
ExecStart=/home/admin/1688-auto-purchase/venv/bin/python3 agent/agent_main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 3. Nginx 反向代理（可选）

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400s;
    }
}
```

### 4. 独立模式（不用管理面板）

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
| `search.search_mode` | 搜索模式：`image`（搜图）/ `shop`（搜店）| image |
| `cart.target_amount` | 采购目标金额（元） | 2000 |
| `cart.purchase_mode` | 采购模式：`normal` / `new_product` | normal |
| `cart.amount_strategy` | 金额策略：`not_exceed`（不超目标）| not_exceed |
| `cart.order_limit` | 每笔订单金额上限（元） | 500 |
| `cart.shipping_reserve` | 每单预留运费（元） | 15 |
| `cart.max_items` | 最大加购商品数 | 200 |

## 项目结构

```
├── main.py                  # 独立模式入口
├── bootstrap.py             # 环境准备（安装 Playwright 等）
├── config.json              # 本地配置
├── config_editor.py         # 交互式配置编辑器
│
├── src/                     # 采购核心逻辑
│   ├── browser.py           # 浏览器初始化与反检测
│   ├── login.py             # 登录等待与验证码检测
│   ├── search.py            # 以图搜图 / 搜店
│   ├── shop.py              # 店铺定位、新品专区、翻页、客服消息、店铺验证
│   ├── cart.py              # 加购、销量排序、去重、分组结算、起批检测
│   ├── purchase_history.py  # 采购去重（管理端 API 共享）
│   ├── retry.py             # 错误重试、熔断器、验证码检测
│   ├── selector_health.py   # 选择器健康检测
│   ├── report.py            # 运行报告
│   └── utils.py             # 工具函数
│
├── agent/                   # 采购端 Agent
│   ├── agent_main.py        # Agent 入口（命令路由）
│   ├── ws_client.py         # WebSocket 客户端（自动重连、心跳）
│   ├── worker.py            # 状态机任务执行器（暂停/恢复/检查点）
│   └── log_interceptor.py   # 结构化日志转发
│
├── server/                  # 管理端服务器
│   ├── server_main.py       # 服务器入口
│   ├── app.py               # FastAPI 应用（中间件、路由注册）
│   ├── api/                 # REST API
│   │   ├── auth.py          # 认证（Session + SQLite）
│   │   ├── users.py         # 用户管理
│   │   ├── nodes.py         # 节点管理（CRUD + 代码更新）
│   │   ├── tasks.py         # 任务控制（启停/暂停/恢复/审批）
│   │   ├── task_plans.py    # 任务计划
│   │   ├── configs.py       # 节点配置
│   │   ├── images.py        # 图片上传管理
│   │   ├── reports.py       # 报告汇总与统计
│   │   ├── logs.py          # 日志查询
│   │   ├── purchased.py     # 采购历史（全局去重）
│   │   └── audit.py         # 操作审计
│   ├── ws/                  # WebSocket
│   │   └── agent_handler.py # Agent 连接、注册、消息路由
│   ├── db/                  # 数据库
│   │   └── database.py      # SQLite + 自动迁移
│   ├── services/            # 服务层
│   │   └── node_manager.py  # 节点状态管理 + Dashboard 推送
│   └── static/              # 前端
│       └── index.html       # Vue 3 + Element Plus SPA（多端适配）
│
├── shared/                  # Agent/Server 共享
│   └── protocol.py          # WebSocket 消息类型与状态定义
│
├── requirements.txt         # 采购端依赖
├── requirements-agent.txt   # Agent 依赖
└── requirements-server.txt  # 服务器依赖
```

## 管理面板

| 页面 | 功能 |
|------|------|
| 总览 | 6 统计卡片 + 节点状态卡片（在线/离线/进度/启停/暂停/恢复/结算审批）|
| 节点管理 | 节点卡片（详情/配置/启动/暂停/继续/停止/更新代码）+ 实时日志面板（事件/运行/全部视图、级别筛选、搜索）|
| 任务中心 | 创建多节点采购计划、查看执行状态、一键执行 |
| 数据中心 | ECharts 趋势图 + 按节点统计 + 采购记录表格 + CSV 导出 |
| 用户管理 | 创建/编辑/禁用用户、角色管理 |
| 操作日志 | 所有操作的审计记录 |

## 通信协议

采购服务器（仅出站）通过 WebSocket 主动连接管理服务器：

| 类型 | 方向 | 说明 |
|------|------|------|
| `register` | Agent→Server | 注册认证（node_id + token）|
| `heartbeat` | Agent→Server | 心跳保活（15秒/次）|
| `start_task` | Server→Agent | 下发配置+图片，启动采购 |
| `stop_task` | Server→Agent | 中止任务 |
| `pause_task` | Server→Agent | 暂停任务（下一个检查点阻塞）|
| `resume_task` | Server→Agent | 恢复任务（从断点继续）|
| `approve_checkout` | Server→Agent | 批准结算 |
| `reject_checkout` | Server→Agent | 拒绝结算 |
| `update_code` | Server→Agent | 触发 git pull + 自动重启 |
| `status_update` | Agent→Server | 状态变更（含暂停状态）|
| `progress_update` | Agent→Server | 加购进度（件数、金额、页码）|
| `log_entry` | Agent→Server | 结构化日志（事件/运行分类）|
| `task_report` | Agent→Server | 任务完成报告 |

## 任务状态流转

```
starting → waiting_login → logged_in → searching → entering_shop
    → filling_cart → cart_filled → awaiting_approval → checking_out → completed
                                                    ↘ rejected → completed

任何阶段均可：→ paused（暂停）→ 恢复到原状态
任何阶段均可：→ cancelled（取消）
任何阶段均可：→ failed（异常）
```

## License

MIT
