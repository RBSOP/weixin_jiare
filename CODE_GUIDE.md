# 微信视频号加热平台数据采集 - 代码讲解文档

## 一、整体架构

项目采用「Web 展示 + 后台采集」双线程架构：

```
┌─────────────────────────────────────────────────────────────────┐
│                         main.py 主进程                            │
│  ┌──────────────────┐    ┌─────────────────────────────────────┐ │
│  │ Flask Web 服务    │    │ Playwright 采集循环（轮询 pending）   │ │
│  │ (daemon 线程)     │    │ - add_account / open_browser / collect│ │
│  └────────┬─────────┘    └─────────────────────────────────────┘ │
│           │                         ▲                            │
│           │  pending_action.json    │                             │
│           └─────────────────────────┘                             │
└─────────────────────────────────────────────────────────────────┘
```

- **Web 服务**：提供数据展示、账号管理、导出、配置等 API
- **采集循环**：主线程轮询 `pending_action.json`，收到动作后执行对应采集逻辑
- **通信方式**：Web 写入 JSON 文件，主进程读取并清除

## 二、核心模块说明

### 1. main.py - 主入口

**职责**：启动 Web 服务、初始化浏览器、轮询执行采集动作。

**关键逻辑**：
- `_load_ui_config()`：读取 `data/ui_config.json`，获取模式（prod/dev）和分辨率
- `_run_collect_flow()`：执行完整采集流程，对每页订单依次调用：
  - `collect_create_order_config` → 再来一单配置
  - `collect_detail_data` → 订单详情
  - `collect_ecommerce_statistic` → 电商加热效果
  - `collect_people_statistic` → 人群分析
- `read_and_clear()`：读取并清除待执行动作，支持 `add_account`、`open_browser`、`collect`

### 2. config.py - 配置

集中管理 URL、路径、业务参数：

- **登录/首页**：`LOGIN_URL`、`HOME_URL`、`ORDER_LIST_URL`
- **接口特征**：`ORDER_API_PATTERNS` 用于响应拦截匹配
- **存储**：`DB_PATH`、`ACCOUNT_COOKIE_DIR`、`SCREENSHOT_DIR`
- **业务**：`COST_MIN`（消耗过滤）、`FULL_RESYNC`（全量重采）

### 3. db.py - 数据库

使用 SQLite，表结构：

| 表名 | 说明 |
|------|------|
| `accounts` | 账号信息（id, account_name, video_account, cookie_path） |
| `orders` | 订单列表（promotion_id, nick_name, cost_yuan, status 等） |
| `order_create_config` | 再来一单配置（config_json） |
| `order_detail_data` | 订单详情（加热信息、消耗进度、效果汇总、十分钟级数据） |
| `order_ecommerce_statistic` | 电商加热效果（merged_json） |
| `order_people_statistic` | 人群分析（funnel_json, user_feature_json） |

**关键函数**：
- `save_orders()`：订单 upsert，支持 `account_id` 关联
- `save_order_create_config()` / `save_order_detail_data()` 等：各维度数据保存
- `get_existing_promotion_ids()`：增量采集边界检测
- `is_order_detail_collected()`：判断订单是否已完成详情链采集

### 4. login.py / home.py - 登录与首页

- `login.wait_for_login()`：等待 URL 跳转，判断登录成功（`LOGIN_SUCCESS_URL_PATTERN`）
- `home.goto_home()`：导航到首页
- `home.wait_for_home()`：等待首页加载完成

### 5. account_collector.py - 账号采集

**流程**：登录页 → 扫码 → 首页 → 账户信息页 → 提取账号名/视频号 → 保存 cookie

- `extract_account_from_home()`：拦截 `getUserPrepare` 等 API，从 `corporateUserInfo` 提取账户名、视频号
- `add_account_flow()`：完整添加账号流程，同名账号则更新

### 6. order_collector.py - 订单列表采集

**两种模式**：
- `collect_order_data()`：一次性加载全部页，再筛选入库
- `collect_order_data_page_by_page()`：翻一页采一页，每页未采集订单立即执行详情链

**核心逻辑**：
- 监听 `searchLivePromotionOrderList`、`searchRoi2PromotionOrderList` 响应
- 筛选：状态=已完成、时间范围、消耗>COST_MIN、未入库
- 增量：`FULL_RESYNC=False` 时，遇已存在 `promotion_id` 则停止翻页
- 日期筛选：`_apply_order_list_date_filter()` 在页面设置开始/结束日期并触发查询

### 7. create_order_collector.py - 再来一单配置采集

**流程**：构造 URL 跳转 create-order 页 → 拦截 `getLivePromotionOrderDetail` → 解析为结构化配置

**配置结构**（按大标题分组）：
- 选择加热类型、选择加热对象、选择加热方案、预算与时间、人群定向、其他

**特殊处理**：
- `_extract_audience_interests_from_popup()`：点击观众兴趣框，弹窗采集已选标签
- `_extract_audience_cities_from_popup()`：观众城市弹窗采集
- `_extract_fan_authors_from_popup()`：粉丝层推荐弹窗采集
- `_extract_from_page()`：从 DOM 直接读取预计金额、成交ROI 等

### 8. order_detail_collector.py - 订单详情采集

**流程**：跳转详情页 → 拦截 `getLivePromotionOrderDetail`、`getLivePromotionOrdersTsIndicator`

**采集内容**：
- **加热信息**：订单名称、编号、加热目标、状态、预算、时间、人群定向等
- **消耗进度**：消耗微信豆、预算微信豆、消耗进度
- **直播间加热效果**：消耗总金额、曝光/进入/点赞/评论/新增关注
- **十分钟级数据**：时间序列，每时间点含消耗、曝光、观看、点赞等

**兜底**：API 不完整时，`_extract_heating_info_from_dom()`、`_extract_effect_summary_from_dom()` 从 DOM 提取

### 9. order_statistic_collector.py - 电商加热效果采集

**流程**：从详情页点击「查看详情」→ 进入电商加热效果页 → 拦截 `getLivePromotionOrderOverview`、`searchLivePromotionOrderList`

**采集内容**：
- 12 项自选指标：总消耗、直接成交金额、直接成交ROI、直播间曝光/观看、商品点击率等
- 9 项数据明细：加热主播与创建时间、名称与编号、加热时间、进入率、GPM、转化率等

**去重**：`_merge_dedup()` 合并 API 与 DOM 数据，`PREFER_DOM_KEYS` 优先使用 DOM 值

### 10. people_statistic_collector.py - 人群分析采集

**流程**：从电商页切换或直接跳转人群分析页 → 拦截 `getLivePromotionOrderUserFeature`、`getLivePromotionOrderOverview`

**采集内容**：
- **人群漏斗**：直播间曝光→观看→商品曝光→商品点击→下单→成交，及各转化率
- **人群分布**：观众商品偏好、八类人群占比、性别/年龄/地域分布

### 11. app.py - Web 服务

**路由**：

| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 数据展示页 |
| `/api/data` | GET | 分页查询订单及关联数据 |
| `/api/export` | GET | 导出 Excel |
| `/api/accounts` | GET | 账号列表 |
| `/api/accounts/<id>` | DELETE | 删除账号 |
| `/api/request_add_account` | POST | 请求添加账号 |
| `/api/request_open_browser` | POST | 请求打开账号浏览器 |
| `/api/request_collect` | POST | 请求采集 |
| `/api/config` | GET/POST | 获取/保存 UI 配置 |
| `/api/clear_all_data` | POST | 清空所有订单数据 |
| `/api/screenshot/<pid>/<type>` | GET | 获取页面截图 |
| `/api/logs` | GET | 获取最近日志 |

**数据扁平化**：`db_query.query_orders_with_relations()` 将多表关联数据扁平为单行，列名带前缀（如 `orders_`、`detail_加热信息_`、`ecommerce_`）。

### 12. db_query.py - 数据查询

- `query_orders_with_relations()`：订单 + create_config + detail + ecommerce + people 联合查询
- `_flatten()`：递归扁平化嵌套 dict/list
- `_fill_people_feature_block()`：人群分布按占比降序填充到固定列
- 十分钟级数据：每时间点一行，其他列复用订单行

### 13. export_xlsx.py - Excel 导出

- `_sort_export_cols()`：按分类、列顺序排序
- `get_category_colors()`：分类对应表头颜色
- `format_export_value()`：数值、时间、状态等格式化
- `add_screenshot_image()`：将截图嵌入单元格

### 14. pending_action.py - 动作通信

- `write(action, **kwargs)`：写入 `data/pending_action.json`
- `read_and_clear()`：读取后删除文件，返回动作数据

### 15. 辅助模块

- **create_order.py**：`build_create_order_url()`、`goto_create_order()` 构造并跳转再来一单 URL
- **order_detail.py**：`build_detail_url()`、`goto_detail_page()` 构造并跳转详情页 URL
- **screenshot_util.py**：`capture_page_screenshot()` 按 promotion_id、页面类型保存截图
- **port_util.py**：`ensure_port_free()` 检测并释放端口
- **logger.py**：统一日志格式，输出到 `logs/collect.log`

## 三、数据流示意

```
订单列表 API (searchLivePromotionOrderList)
    → 筛选（已完成、时间、消耗、未入库）
    → save_orders() → orders 表

对每订单（消耗>COST_MIN 且未采详情链）：
    1. create-order 页 → getLivePromotionOrderDetail
       → save_order_create_config() → order_create_config 表
    2. 详情页 → getLivePromotionOrderDetail + getLivePromotionOrdersTsIndicator
       → save_order_detail_data() → order_detail_data 表
    3. 电商页（点击查看详情）→ getLivePromotionOrderOverview + searchLivePromotionOrderList
       → save_order_ecommerce_statistic() → order_ecommerce_statistic 表
    4. 人群页 → getLivePromotionOrderUserFeature + getLivePromotionOrderOverview
       → save_order_people_statistic() → order_people_statistic 表
```

## 四、扩展与定制

- **修改消耗阈值**：`config.COST_MIN`
- **修改采集范围**：`order_collector` 中 `_filter_by_cost`、`_filter_completed_only`、`_in_time_range`
- **新增采集字段**：在对应 collector 的解析函数中扩展，同步更新 `db` 表结构及 `db_query` 扁平化逻辑
- **新增 API 路由**：在 `app.py` 添加路由，必要时在 `db_query` 或 `db` 中扩展查询
