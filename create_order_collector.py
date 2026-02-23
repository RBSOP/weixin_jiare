"""create-order 页面数据采集 - 拦截 getLivePromotionOrderDetail，解析并保存配置"""
import json

from playwright.sync_api import Page, Response

from db import save_order_create_config
from logger import TAG_CREATE_ORDER, log

API_PATTERN = "getLivePromotionOrderDetail"


def _parse_json_body(body: bytes) -> dict | None:
    text = body.decode("utf-8", errors="replace")
    return json.loads(text)


def _map_promotion_type(v) -> str:
    """加热对象类型：1=短视频 2=直播间"""
    m = {1: "短视频", 2: "直播间"}
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_order_type(v) -> str:
    """加热订单类型：1=标准订单 2=全域订单 3=长期计划"""
    m = {1: "标准订单", 2: "全域订单", 3: "长期计划"}
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_heating_method(v) -> str:
    """加热方式：promotionTarget 等"""
    m = {11: "成交ROI", 10: "观看量", 12: "互动量"}
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_delivery_speed(v) -> str:
    """放量模式：1=匀速放量 2=快速放量"""
    m = {1: "匀速放量", 2: "快速放量"}
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_material_flag(v) -> str:
    """加热素材：1=直接加热直播间 2=短视频加热直播间"""
    m = {1: "直接加热直播间", 2: "短视频加热直播间"}
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_gender(v) -> str:
    """观众性别：0=不限 1=男 2=女（suggest.gender 可能为数组如 [2]）"""
    m = {0: "不限", 1: "男", 2: "女"}
    if isinstance(v, list) and v:
        v = v[0]
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_age_range(codes) -> list[str]:
    """观众年龄：1=18-23 2=24-30 3=31-40 4=41-50 5=51岁以上（suggest.ageRange 为数组如 [4,5]）"""
    m = {1: "18-23岁", 2: "24-30岁", 3: "31-40岁", 4: "41-50岁", 5: "51岁以上"}
    if not codes:
        return []
    return [m.get(int(c), str(c)) for c in (codes if isinstance(codes, list) else [codes]) if c is not None]


def _parse_detail_to_config(data: dict) -> dict | None:
    """
    将 getLivePromotionOrderDetail 响应解析为带大标题的结构化配置
    """
    raw = data.get("data") or {}
    info = raw.get("livePromotionOrderDetailInfo") or raw.get("orderInfo") or raw
    if not info:
        return None

    order_info = info.get("orderInfo") or info
    acct = order_info.get("acctInfo") or {}
    suggest = order_info.get("suggest") or {}
    payment_info = order_info.get("paymentInfo") or {}

    cost_quota = info.get("costQuota") or order_info.get("costQuota") or 0
    cost_yuan = int(cost_quota) / 10.0 if cost_quota else 0
    duration = info.get("duration") or order_info.get("duration") or order_info.get("promotionDuration") or ""
    duration_str = f"{duration}小时" if duration else ""

    gender = suggest.get("gender")
    age_range = suggest.get("ageRange") or suggest.get("ageRangeList") or []
    city_ids = suggest.get("cityIds") or []
    interest_tag = suggest.get("interestTagV3") or suggest.get("interestTag") or []

    interest_labels = []
    if isinstance(interest_tag, list):
        for t in interest_tag:
            if isinstance(t, dict):
                interest_labels.append(t.get("name") or t.get("label") or str(t))
            else:
                interest_labels.append(str(t))
    if interest_labels:
        interest_str = f"已添加{interest_labels[0]}等{len(interest_labels)}个标签"
    else:
        interest_str = "未添加"

    city_str = "全部地区" if not city_ids else f"已选{len(city_ids)}个城市"

    fan_target = suggest.get("fanTarget") or suggest.get("similarAuthorFan")
    list_target = suggest.get("listTarget") or suggest.get("specifiedList")
    targeting_type = "自定义" if (gender is not None or age_range or city_ids or interest_tag) else "全部"
    fan_recommend = "选择相似作者的粉丝" if fan_target else "不限"
    list_recommend = "选择指定名单" if list_target else "不限"

    device = suggest.get("deviceTypes") or suggest.get("device") or suggest.get("deviceType")
    device_str = "不限"
    if device:
        if isinstance(device, list):
            if 1 in device and 2 not in device:
                device_str = "iOS"
            elif 2 in device and 1 not in device:
                device_str = "安卓"
        elif device == 1:
            device_str = "iOS"
        elif device == 2:
            device_str = "安卓"

    voucher_info = payment_info.get("voucherInfo") or {}
    has_voucher = bool(voucher_info.get("voucherId") or voucher_info.get("couponId"))
    growth_card = payment_info.get("growthCardInfo") or {}
    has_growth = bool(growth_card.get("cardId"))
    cost_guarantee = payment_info.get("costGuaranteeInfo") or {}
    has_guarantee = bool(cost_guarantee.get("voucherId"))
    if has_voucher:
        payment_str = "优惠券"
    elif has_growth:
        payment_str = "电商成长卡"
    elif has_guarantee:
        payment_str = "成本保障券"
    else:
        payment_str = "不使用"

    roi = order_info.get("bidRoi") or order_info.get("targetRoi") or suggest.get("bidRoi")
    if roi is None and suggest.get("roiBidX100") is not None:
        roi = suggest["roiBidX100"] / 100.0
    bid_roi = str(roi) if roi is not None else ""
    order_name = order_info.get("orderName") or order_info.get("promotionName") or ""

    config = {
        "选择加热类型": {
            "加热对象类型": _map_promotion_type(order_info.get("promotionType")),
            "加热订单类型": _map_order_type(order_info.get("orderType")),
        },
        "选择加热对象": {
            "主播昵称": acct.get("nickName") or "",
        },
        "选择加热方案": {
            "预计带来商品成交金额": f"{cost_yuan:.0f}元" if cost_yuan else "",
            "基础信息": {
                "订单名称": order_name,
                "加热方式": _map_heating_method(order_info.get("promotionTarget")),
                "放量模式": _map_delivery_speed(order_info.get("deliverySpeedMode")),
                "优先提升目标": _map_heating_method(order_info.get("promotionTarget")),
                "成交ROI": str(bid_roi) if bid_roi else "",
                "加热素材": _map_material_flag(order_info.get("materialFlag")),
            },
        },
        "预算与时间": {
            "订单预算": f"{cost_yuan:.0f}元" if cost_yuan else "",
            "加热时长": duration_str,
        },
        "人群定向": {
            "定向类型": targeting_type,
            "观众性别": _map_gender(gender),
            "根据粉丝层推荐": fan_recommend,
            "根据名单推荐": list_recommend,
            "观众年龄": _map_age_range(age_range),
            "观众设备": device_str,
            "观众城市": city_str,
            "观众兴趣": interest_str,
        },
        "其他": {
            "其他支付方式": payment_str,
        },
    }
    return config


def collect_create_order_config(page: Page, promotion_id: str) -> dict | None:
    """
    跳转到 create-order 页面，拦截 getLivePromotionOrderDetail，解析并保存配置
    :return: 解析后的配置 dict，失败返回 None
    """
    from create_order import goto_create_order

    collected: list[dict] = []

    def on_response(response: Response):
        if API_PATTERN not in response.url:
            return
        if response.status not in (200, 201) or not response.body():
            return
        data = _parse_json_body(response.body())
        if data and data.get("errCode") == 0:
            collected.append(data)

    page.on("response", on_response)
    log(TAG_CREATE_ORDER, f"跳转 create-order | pid={promotion_id}")
    goto_create_order(page, promotion_id)
    page.wait_for_timeout(3000)

    if not collected:
        log(TAG_CREATE_ORDER, f"未获取到接口数据 | pid={promotion_id}")
        return None

    last = collected[-1]
    config = _parse_detail_to_config(last)
    if config:
        save_order_create_config(promotion_id, config)
        log(TAG_CREATE_ORDER, f"解析并保存成功 | pid={promotion_id}")
    else:
        log(TAG_CREATE_ORDER, f"解析失败 | pid={promotion_id}")
    return config
