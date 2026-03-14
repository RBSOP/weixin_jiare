"""项目配置"""
import os

# 加热平台登录页
LOGIN_URL = "https://channels.weixin.qq.com/login.html?from=promote"

# 登录成功后跳转的 URL 特征（用于判断登录完成）
LOGIN_SUCCESS_URL_PATTERN = "channels.weixin.qq.com/promote"

# 首页 URL（登录后默认进入）
HOME_URL = "https://channels.weixin.qq.com/promote/pages/platform/home"

# 首页 URL 特征（用于判断已在首页）
HOME_URL_PATTERN = "/promote/pages/platform/home"

# 直播订单列表页（我的订单）
ORDER_LIST_URL = "https://channels.weixin.qq.com/promote/pages/platform/live/order-list"

# 订单相关接口 URL 特征（用于拦截抓取）
ORDER_API_PATTERNS = [
    "searchLivePromotionOrderList",  # 直播推广订单列表
    "searchRoi2PromotionOrderList",   # 成交ROI订单列表
    "getLivePromotionOrderOverview",  # 订单概览
    "getLivePromotionOrderDetail",      # 订单详情（再来一单/详情页）
    "getLivePromotionOrdersTsIndicator",  # 详情页十分钟级时间线及效果汇总
]

# 登录状态持久化文件（可选，用于下次免扫码）
AUTH_STATE_FILE = os.path.join(os.path.dirname(__file__), "auth_state.json")

# 账号 cookie 存储目录（每个账号一个文件）
ACCOUNT_COOKIE_DIR = os.path.join(os.path.dirname(__file__), "data", "account_cookies")

# 页面截图存储目录（按 promotion_id 分目录）
SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "data", "screenshots")

# 采集数据输出目录（已弃用，改用数据库）
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# SQLite 数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "orders.db")

# 订单过滤：仅采集消耗 > 此值的订单（单位：元，API 中 cost 为微信豆 1元=10豆）
COST_MIN = 500

# 翻页上限，None 表示不限制（增量采集时遇已存在会提前停止）
MAX_PAGES = None

# 全量重采：设为 True 时订单列表不按库内 ID 提前停止，从头翻到底
FULL_RESYNC = False

# 再来一单页面 base URL
CREATE_ORDER_BASE = "https://channels.weixin.qq.com/promote/pages/platform/create-order"

# 订单详情页 base URL（从列表点击「详情」进入）
ORDER_DETAIL_BASE = "https://channels.weixin.qq.com/promote/pages/platform/live/live-promote-order-detail-new"

# 电商加热效果统计页（从详情页「查看详情」进入）
ECOMMERCE_STATISTIC_URL = "https://channels.weixin.qq.com/promote/pages/platform/live/live-promote-statistic/order"

# 人群分析页（与 order 同模块，路径 /people）
PEOPLE_STATISTIC_URL = "https://channels.weixin.qq.com/promote/pages/platform/live/live-promote-statistic/people"

# 日志配置
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
LOG_FILE = os.path.join(LOG_DIR, "collect.log")
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5MB
LOG_BACKUP_COUNT = 5

# Web 展示页
WEB_HOST = "0.0.0.0"
WEB_PORT = 9527
UI_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "data", "ui_config.json")
PENDING_ACTION_FILE = os.path.join(os.path.dirname(__file__), "data", "pending_action.json")
