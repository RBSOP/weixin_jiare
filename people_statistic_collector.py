"""人群分析页采集 - 人群漏斗、观众商品偏好、八类人群、性别/年龄/地域分布"""
import json
from urllib.parse import urlencode

from playwright.sync_api import Page, Response

from config import PEOPLE_STATISTIC_URL
from db import save_order_people_statistic
from logger import TAG_PEOPLE, log
from screenshot_util import capture_page_screenshot

API_USER_FEATURE = "getLivePromotionOrderUserFeature"
API_OVERVIEW = "getLivePromotionOrderOverview"

# 行政区划代码 -> 省份/城市名（常见）
REGION_CODE_MAP = {
    "0": "其他",
    "110000": "北京市",
    "310000": "上海市",
    "440000": "广东省",
    "441900": "广东省东莞市",
    "350000": "福建省",
    "350500": "福建省漳州市",
    "520000": "贵州省",
    "522700": "贵州省黔南州",
}


def _region_name(code: str) -> str:
    if not code:
        return "其他"
    s = str(code)
    if s in REGION_CODE_MAP:
        return REGION_CODE_MAP[s]
    if len(s) >= 2:
        province = {"11": "北京市", "31": "上海市", "44": "广东省", "35": "福建省", "52": "贵州省"}.get(s[:2], "")
        if province:
            return province
    return s


def _parse_json(body: bytes) -> dict | None:
    text = body.decode("utf-8", errors="replace")
    return json.loads(text)


def _int(v, default=0):
    if v is None:
        return default
    if isinstance(v, str):
        return int(v) if v.isdigit() else default
    return int(v)


def _parse_funnel(overview: dict) -> dict:
    """从 getLivePromotionOrderOverview 解析人群漏斗"""
    raw = overview.get("data") or {}
    info = raw.get("promotionDataInfoSum") or raw.get("livePromotionDataInfoSum") or {}

    exposure = _int(info.get("exposureCount"))
    join_count = _int(info.get("joinCount"))
    product_exp_uv = _int(info.get("productExposureCountUv"))
    product_click_uv = _int(info.get("productClickCountUv"))
    order_uv = _int(info.get("liveProductOrderUv"))
    pay_uv = _int(info.get("liveProductPayUv"))

    total_convert_rate = (pay_uv / exposure * 100) if exposure else 0
    live_click_rate = (join_count / exposure * 100) if exposure else 0
    product_exp_rate = (product_exp_uv / join_count * 100) if join_count else 0
    product_click_rate = (product_click_uv / product_exp_uv * 100) if product_exp_uv else 0
    click_order_rate = (order_uv / product_click_uv * 100) if product_click_uv else 0
    order_pay_rate = (pay_uv / order_uv * 100) if order_uv else 0

    return {
        "直播间曝光人数": exposure,
        "直播间观看人数": join_count,
        "商品曝光人数": product_exp_uv,
        "商品点击人数": product_click_uv,
        "总下单人数": order_uv,
        "总成交人数": pay_uv,
        "直播间点击率": round(live_click_rate, 2),
        "商品曝光率": round(product_exp_rate, 2),
        "商品点击率": round(product_click_rate, 2),
        "点击下单率": round(click_order_rate, 2),
        "下单成交率": round(order_pay_rate, 2),
        "总成交转化率": round(total_convert_rate, 4),
    }


def _parse_user_feature(data: dict) -> dict:
    """从 getLivePromotionOrderUserFeature 解析人群分布"""
    raw = data.get("data") or {}
    result = {}

    def _rows_to_list(name: str, rows_key: str) -> list:
        obj = raw.get(rows_key) or {}
        rows = obj.get("rows") or []
        return [
            {"key": r.get("key", ""), "value": _int(r.get("value")), "percent": round(_int(r.get("percentInCents")) / 100.0, 2)}
            for r in rows
        ]

    result["观众商品偏好"] = _rows_to_list("观众商品偏好", "productDistribution")
    result["八类人群占比"] = _rows_to_list("八类人群占比", "crowdDistribution")
    result["性别分布"] = _rows_to_list("性别分布", "genderDistribution")
    result["年龄分布"] = _rows_to_list("年龄分布", "ageDistribution")

    city_rows = (raw.get("cityDistribution") or {}).get("rows") or []
    result["地域分布"] = [
        {"name": _region_name(r.get("key")), "value": _int(r.get("value")), "percent": round(_int(r.get("percentInCents")) / 100.0, 2)}
        for r in city_rows
    ]
    return result


def _build_people_url(promotion_id: str) -> str:
    """构造人群分析页 URL（支持 promotionId 参数以预选订单）"""
    return f"{PEOPLE_STATISTIC_URL}?{urlencode({'promotionId': promotion_id})}"


def collect_people_statistic(page: Page, promotion_id: str) -> dict | None:
    """
    直接 URL 跳转人群分析页，采集漏斗+人群分布
    :param page: Playwright 页面
    :return: 合并后的统计 dict，失败返回 None
    """
    user_feature_responses = []
    overview_responses = []

    def on_response(response: Response):
        if "channels.weixin.qq.com" not in response.url:
            return
        if response.status not in (200, 201):
            return
        try:
            body = response.body()
            if not body:
                return
            data = _parse_json(body)
        except Exception:
            return
        if not data:
            return
        err = data.get("errCode")
        if err is not None and err != 0:
            return
        if API_USER_FEATURE in response.url:
            user_feature_responses.append(data)
        elif API_OVERVIEW in response.url:
            overview_responses.append(data)

    page.on("response", on_response)

    current_url = page.url
    if "live-promote-statistic" in current_url:
        people_url = current_url.replace("/order", "/people")
        log(TAG_PEOPLE, f"从统计页切换到人群分析 | pid={promotion_id}")
    else:
        people_url = _build_people_url(promotion_id)
        log(TAG_PEOPLE, f"跳转人群分析页 | pid={promotion_id}")

    page.goto(people_url, wait_until="domcontentloaded")
    page.wait_for_timeout(8000)
    page.remove_listener("response", on_response)

    funnel = _parse_funnel(overview_responses[-1]) if overview_responses else {}
    user_feature = _parse_user_feature(user_feature_responses[-1]) if user_feature_responses else {}

    if funnel or user_feature:
        save_order_people_statistic(promotion_id, funnel, user_feature)
        log(TAG_PEOPLE, f"保存完成 | pid={promotion_id} | 漏斗={bool(funnel)} | 人群分布={bool(user_feature)}")
        capture_page_screenshot(page, promotion_id, "people", TAG_PEOPLE)
        return {"人群漏斗": funnel, "人群分布": user_feature}
    log(TAG_PEOPLE, f"无有效数据 | pid={promotion_id} | overview={len(overview_responses)} | user_feature={len(user_feature_responses)}")
    capture_page_screenshot(page, promotion_id, "people", TAG_PEOPLE)
    return None
