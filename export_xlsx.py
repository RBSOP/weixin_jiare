"""xlsx 导出模块 - 与页面展示一致：列顺序、格式化、分类颜色、截图"""
import re
from pathlib import Path

# 截图列（与页面 SCREENSHOT_COLS 一致）
SCREENSHOT_COLS = [
    "screenshot_create_config",
    "screenshot_order_detail",
    "screenshot_ecommerce",
    "screenshot_people",
]
SCREENSHOT_LABELS = {
    "screenshot_create_config": "再来一单配置",
    "screenshot_order_detail": "订单详情",
    "screenshot_ecommerce": "电商加热效果",
    "screenshot_people": "人群分析",
}

# 分类 -> 表头行1颜色（深色）、表头行2/数据行颜色（浅色），与 index.html CSS 一致
_CATEGORY_COLORS = {
    "订单基本信息": ("1989FA", "ECF5FF"),
    "再来一单配置": ("07C160", "EDFFF3"),
    "加热信息": ("0D9488", "CCFBF1"),
    "直播间加热效果": ("FF976A", "FFF5F0"),
    "电商加热效果(详情)": ("EE0A24", "FFF0F0"),
    "十分钟级数据": ("9C27B0", "F3E5F5"),
    "电商加热效果": ("EE0A24", "FFF0F0"),
    "人群漏斗": ("7232DD", "F5EFFF"),
    "观众商品偏好": ("ED6A0C", "FFF5EB"),
    "八类人群占比": ("E91E63", "FCE4EC"),
    "性别分布": ("00BCD4", "E0F7FA"),
    "年龄分布": ("8BC34A", "F1F8E9"),
    "地域分布": ("FF9800", "FFF3E0"),
    "截图": ("607D8B", "ECEEF1"),
    "其他": ("969799", "F7F8FA"),
}

_STATUS_MAP = {
    "0": "待审核",
    "1": "待开始",
    "2": "投放中",
    "3": "已完成",
    "4": "已拒绝",
    "5": "已取消",
    "6": "已暂停",
    "7": "审核中",
    "8": "已预约",
    "9": "预约取消",
}


def format_ts(ts) -> str:
    """时间戳转 YYYY-MM-DD HH:mm:ss"""
    if ts is None or ts == "":
        return ""
    s = str(ts).strip()
    if not re.match(r"^\d{9,10}$", s):
        return s
    try:
        from datetime import datetime

        dt = datetime.fromtimestamp(int(s))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return s


def format_status(v) -> str:
    return _STATUS_MAP.get(str(v), str(v) if v is not None else "")


def format_duration(sec) -> str:
    if sec is None or sec == "":
        return "-"
    try:
        s = int(sec)
    except (ValueError, TypeError):
        return "-"
    if not s:
        return "-"
    h = s // 3600
    m = (s % 3600) // 60
    if h > 0 and m > 0:
        return f"{h}小时{m}分钟"
    if h > 0:
        return f"{h}小时"
    return f"{m}分钟"


def format_budget(v) -> str:
    try:
        n = float(v)
    except (ValueError, TypeError):
        return "-"
    if not n:
        return "-"
    return f"{n / 10:.0f}"


def format_interest(v) -> str:
    """观众兴趣：顿号/换行替换为逗号"""
    if not v:
        return ""
    s = str(v).replace("、", "，").replace("\n", "，")
    s = re.sub(r"，+", "，", s).strip("，")
    return s


def format_export_value(col: str, v) -> str:
    """与页面 fmtCellVal 一致的格式化"""
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return str(v)
    if col == "orders_create_time" and v and re.match(r"^\d{9,10}$", str(v)):
        return format_ts(v)
    if col == "orders_status":
        return format_status(v)
    if col == "orders_duration":
        return format_duration(v)
    if col == "orders_budget":
        return format_budget(v)
    if col == "orders_cost_yuan":
        try:
            n = float(v)
            return f"{n:.1f}" if n else str(v)
        except (ValueError, TypeError):
            return str(v)
    if col == "orders_actual_roi":
        try:
            n = float(v)
            return f"{n:.2f}" if n else "0"
        except (ValueError, TypeError):
            return "0"
    if col == "ecommerce_直播间商品点击率":
        try:
            n = float(v)
            return f"{n:.2f}%" if not (v == "" or (n != n)) else str(v)
        except (ValueError, TypeError):
            return str(v)
    if col in (
        "ecommerce_直接成交金额",
        "ecommerce_间接成交金额",
        "ecommerce_直播间平均千次展示费用",
        "ecommerce_GPM",
        "ecommerce_直播间转化成本",
    ):
        try:
            n = float(v)
            return f"￥{n:.2f}" if not (v == "" or (n != n)) else str(v)
        except (ValueError, TypeError):
            return str(v)
    if col in ("ecommerce_直播间进入率（人数）", "ecommerce_直播间转化率"):
        try:
            n = float(v)
            return f"{n:.2f}%" if not (v == "" or (n != n)) else str(v)
        except (ValueError, TypeError):
            return str(v)
    if col in (
        "people_funnel_直播间点击率",
        "people_funnel_商品曝光率",
        "people_funnel_商品点击率",
        "people_funnel_点击下单率",
        "people_funnel_下单成交率",
    ):
        try:
            n = float(v)
            return f"{n:.2f}%" if not (v == "" or (n != n)) else str(v)
        except (ValueError, TypeError):
            return str(v)
    if col == "people_funnel_总成交转化率":
        try:
            n = float(v)
            return f"{n:.4f}%" if not (v == "" or (n != n)) else str(v)
        except (ValueError, TypeError):
            return str(v)
    if col in ("create_config_人群定向_观众兴趣", "detail_加热信息_人群定向_观众兴趣"):
        return format_interest(v)
    return str(v)


def get_screenshot_path(promotion_id: str, page_type: str, screenshot_dir: Path) -> Path | None:
    """返回截图文件路径，不存在则返回 None"""
    if not promotion_id:
        return None
    safe_pid = str(promotion_id).replace("/", "_").replace("\\", "_")
    path = screenshot_dir / safe_pid / f"{page_type}.png"
    return path if path.exists() else None


def add_screenshot_image(ws, row: int, col: int, image_path: Path, width: int = 80, height: int = 50) -> bool:
    """
    将原图嵌入 Excel 单元格。
    流程：XLImage(full_path) -> 设置 width/height -> anchor=cell_ref -> add_image(img)
    """
    try:
        from openpyxl.drawing.image import Image as XLImage
        from openpyxl.utils import get_column_letter

        xl_img = XLImage(str(image_path))
        xl_img.width = width
        xl_img.height = height
        cell_ref = f"{get_column_letter(col)}{row}"
        xl_img.anchor = cell_ref
        ws.add_image(xl_img)
        return True
    except Exception:
        return False


def get_category_colors(category: str) -> tuple[str, str]:
    """返回 (表头行1颜色, 表头行2/数据行颜色)"""
    return _CATEGORY_COLORS.get(category, ("969799", "F7F8FA"))
