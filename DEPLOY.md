# 微信视频号加热平台数据采集 - 部署文档

## 一、项目简介

本项目用于自动化采集微信视频号加热平台的订单数据，包括账号管理、订单列表、订单详情、再来一单配置、电商加热效果、人群分析等，并通过 Web 页面展示与导出。

## 二、环境要求

| 项目 | 要求 |
|------|------|
| Python | 3.10 及以上 |
| 操作系统 | Windows / macOS / Linux |
| 浏览器 | Chromium（由 Playwright 自动安装） |
| 网络 | 可访问 `channels.weixin.qq.com` |

## 三、安装步骤

### 1. 克隆或下载项目

```bash
cd weixin_jiare
```

### 2. 创建虚拟环境（推荐）

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
```

### 3. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

依赖包括：
- `playwright` - 浏览器自动化
- `flask` - Web 展示服务
- `openpyxl` - Excel 导出

### 4. 安装 Playwright 浏览器

```bash
playwright install chromium
```

首次运行会下载 Chromium 浏览器，约 150MB。

## 四、目录结构

```
weixin_jiare/
├── main.py                    # 主入口，启动 Web 服务与采集循环
├── app.py                     # Flask Web 服务
├── config.py                  # 全局配置
├── db.py                      # 数据库操作
├── db_query.py                # 数据查询与扁平化
├── login.py                   # 登录逻辑
├── home.py                    # 首页导航
├── account_collector.py       # 账号采集
├── order_collector.py         # 订单列表采集
├── create_order_collector.py  # 再来一单配置采集
├── order_detail_collector.py   # 订单详情采集
├── order_statistic_collector.py # 电商加热效果采集
├── people_statistic_collector.py # 人群分析采集
├── create_order.py            # 再来一单页面跳转
├── order_detail.py            # 订单详情页跳转
├── export_xlsx.py             # Excel 导出
├── pending_action.py         # Web 与主进程通信
├── port_util.py               # 端口检测
├── screenshot_util.py        # 页面截图
├── logger.py                  # 日志
├── requirements.txt           # 依赖
├── start.bat                  # Windows 一键启动
├── data/                      # 数据目录（自动创建）
│   ├── orders.db              # SQLite 数据库
│   ├── ui_config.json         # UI 配置（模式、分辨率）
│   ├── pending_action.json    # 待执行动作（临时）
│   ├── account_cookies/        # 各账号 cookie 文件
│   └── screenshots/           # 页面截图（按 promotion_id 分目录）
├── logs/                      # 日志目录
│   └── collect.log
└── templates/
    └── index.html             # 数据展示页
```

## 五、运行方式

### Windows 用户

双击 `start.bat` 即可启动。

### 命令行启动

```bash
python main.py
```

启动后：
1. 自动打开数据展示页（默认 `http://127.0.0.1:9527`）
2. 进入账号管理页，可添加账号、发起采集
3. 采集过程会打开 Chromium 浏览器窗口，请勿关闭

## 六、配置说明

在 `config.py` 中可修改以下配置：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `WEB_HOST` | 0.0.0.0 | 数据展示页监听地址，0.0.0.0 支持局域网访问 |
| `WEB_PORT` | 9527 | 数据展示页端口 |
| `COST_MIN` | 500 | 仅采集消耗 > 此值的订单（单位：元） |
| `DB_PATH` | data/orders.db | SQLite 数据库路径 |
| `MAX_PAGES` | None | 订单列表翻页上限，None 表示不限制 |
| `FULL_RESYNC` | False | True 时全量重采，不按已入库 ID 提前停止 |

UI 配置（`data/ui_config.json`）可通过数据展示页设置：
- `mode`: `prod` 无头模式 / `dev` 有头模式
- `resolution`: 如 `1280x800`，浏览器窗口分辨率

## 七、首次使用流程

1. **启动程序**：运行 `python main.py` 或双击 `start.bat`
2. **添加账号**：在数据展示页「账号管理」点击「添加账号」
3. **扫码登录**：弹出的浏览器中扫码登录微信视频号加热平台
4. **发起采集**：选择账号，点击「开始采集」，可选时间范围
5. **查看数据**：在「数据展示」页查看、筛选、导出

## 八、注意事项

- **首次运行需扫码登录**：每个账号首次添加需扫码；cookie 保存后可免扫码
- **采集过程勿关闭浏览器**：采集时会打开 Chromium 窗口，关闭会导致采集中断
- **会话过期**：若 cookie 失效，需重新添加账号并扫码
- **端口占用**：若 9527 端口被占用，程序会提示并退出，可修改 `config.py` 中的 `WEB_PORT`
- **局域网访问**：本机 IP + 端口可访问数据展示页，如 `http://192.168.1.100:9527`

## 九、常见问题

### 1. 端口无法释放

程序启动时检测端口，若被占用会退出。可修改 `config.py` 中的 `WEB_PORT` 或关闭占用该端口的程序。

### 2. Playwright 安装失败

确保网络畅通，可尝试：
```bash
playwright install chromium --with-deps
```

### 3. 采集无数据

- 确认账号已正确添加且 cookie 有效
- 检查时间范围是否合理
- 确认订单状态为「已完成」
- 确认消耗金额大于 `COST_MIN`（默认 500 元）

### 4. 导出 Excel 失败

需安装 `openpyxl`：
```bash
pip install openpyxl
```
