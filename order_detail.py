"""订单详情页 - 跳转逻辑"""
from urllib.parse import urlencode

from config import ORDER_DETAIL_BASE


def build_detail_url(promotion_id: str) -> str:
    """
    构造订单详情页 URL
    :param promotion_id: 订单 ID（与列表「名称与编号」中的订单ID一致）
    """
    return f"{ORDER_DETAIL_BASE}?{urlencode({'id': promotion_id})}"


def goto_detail_page(page, promotion_id: str) -> None:
    """
    跳转到指定订单的详情页
    :param page: Playwright 页面
    :param promotion_id: 订单 ID
    """
    url = build_detail_url(promotion_id)
    page.goto(url, wait_until="domcontentloaded")
