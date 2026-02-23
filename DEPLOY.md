# 部署说明

## 环境要求

- Python 3.10+
- Windows / macOS / Linux

## 安装步骤

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 安装 Playwright 浏览器

```bash
playwright install chromium
```

### 3. 目录结构

```
weixin_jiare/
├── main.py              # 主入口
├── app.py               # Web 展示服务
├── config.py            # 配置
├── data/
│   ├── orders.db        # SQLite 数据库（首次运行自动创建）
│   ├── ui_config.json   # UI 配置（模式、分辨率）
│   └── auth_state.json  # 登录状态（可选，免扫码）
├── logs/                # 日志目录
└── templates/           # Web 模板
```

## 运行

```bash
python main.py
```

启动后：
1. 自动打开数据展示页 `http://0.0.0.0:5000`
2. 打开登录页，等待扫码
3. 登录成功后执行采集流程

## 配置说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `WEB_HOST` | 0.0.0.0 | 数据展示页监听地址 |
| `WEB_PORT` | 5000 | 数据展示页端口 |
| `COST_MIN` | 500 | 仅采集消耗 > 此值的订单（元） |
| `DB_PATH` | data/orders.db | SQLite 数据库路径 |

可在 `config.py` 中修改，或通过数据展示页 UI 选择模式、分辨率。

## 注意事项

- 首次运行需扫码登录；若存在 `auth_state.json` 可免扫码
- 采集过程会打开 Chromium 浏览器窗口，请勿关闭
- 数据展示页支持局域网访问，可通过本机 IP 访问后端
