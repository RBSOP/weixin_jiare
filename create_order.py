"""再来一单 - 跳转逻辑与 URL 构造"""
from urllib.parse import urlencode

from config import CREATE_ORDER_BASE


def build_create_order_url(
    promotion_id: str,
    promote_type: str = "live",
    order_type: str = "standard",
) -> str:
    """
    构造「再来一单」页面 URL
    :param promotion_id: 订单 ID（promotion_id）
    :param promote_type: 加热类型：live 直播 / feed 短视频
    :param order_type: 订单类型：standard 标准订单 / full 全域订单 / longterm 长期计划
    """
    params = {
        "promoteType": promote_type,
        "orderType": order_type,
        "sourcePromotionId": promotion_id,
    }
    return f"{CREATE_ORDER_BASE}?{urlencode(params)}"


def goto_create_order(page, promotion_id: str, **kwargs) -> None:
    """
    跳转到指定订单的「再来一单」页面
    :param page: Playwright 页面
    :param promotion_id: 订单 ID
    :param kwargs: 传给 build_create_order_url 的 promote_type、order_type
    """
    url = build_create_order_url(promotion_id, **kwargs)
    page.goto(url, wait_until="domcontentloaded")
