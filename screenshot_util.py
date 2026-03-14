"""页面滚动截图工具 - 各采集模块共用"""
from pathlib import Path

from playwright.sync_api import Page

from config import SCREENSHOT_DIR

# 支持的页面类型及文件名
PAGE_TYPES = ("create_config", "order_detail", "ecommerce", "people")


def capture_page_screenshot(page: Page, promotion_id: str, page_type: str, tag: str = "SCREENSHOT") -> str | None:
    """
    对当前页面进行全页滚动截图
    :param page: Playwright 页面
    :param promotion_id: 订单 ID
    :param page_type: 页面类型 create_config|order_detail|ecommerce|people
    :param tag: 日志标签
    :return: 保存路径，失败返回 None
    """
    if page_type not in PAGE_TYPES:
        return None
    try:
        from logger import log

        safe_pid = promotion_id.replace("/", "_").replace("\\", "_")
        out_dir = Path(SCREENSHOT_DIR) / safe_pid
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{page_type}.png"
        page.screenshot(path=str(path), full_page=True)
        log(tag, f"截图已保存 | pid={promotion_id} | type={page_type} | path={path}")
        return str(path)
    except Exception as e:
        from logger import log

        log(tag, f"截图失败 | pid={promotion_id} | type={page_type} | {e}")
        return None
