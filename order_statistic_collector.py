"""电商加热效果统计采集 - 自选指标 + 数据明细，去重后与订单ID关联"""
import json
from datetime import datetime

from playwright.sync_api import Page, Response

from db import save_order_ecommerce_statistic
from logger import TAG_ECOMMERCE, log, log_error

API_OVERVIEW = "getLivePromotionOrderOverview"
API_ORDER_LIST = "searchLivePromotionOrderList"

# 自选指标与数据明细重复字段（数值相同时只保留一个）
DEDUP_KEYS = {
    "总消耗", "直播间消耗", "直播间曝光人数", "直播间观看人数",
    "直播间商品点击率", "总成交ROI", "直播间平均千次展示费用",
    "总成交金额", "直接成交金额",
}


def _parse_json(body: bytes) -> dict | None:
    text = body.decode("utf-8", errors="replace")
    return json.loads(text)


def _int(v, default=0):
    if v is None:
        return default
    if isinstance(v, str):
        return int(v) if v.isdigit() else default
    return int(v)


def _parse_overview(data: dict) -> dict:
    """从 getLivePromotionOrderOverview 解析自选指标 12 项"""
    raw = data.get("data") or {}
    info = raw.get("promotionDataInfoSum") or raw.get("livePromotionDataInfoSum") or {}
    extra = (raw.get("promotionExtraIndicatorSum") or {}).get("productIndicator") or {}
    live_cost = (raw.get("promotionExtraIndicatorSum") or {}).get("liveCost") or info.get("cost") or 0

    cost = _int(info.get("cost") or live_cost) / 10.0
    exposure = _int(info.get("exposureCount"))
    join_count = _int(info.get("joinCount"))
    join_pv = _int(info.get("joinCountPv"))
    product_exp = _int(info.get("productExposureCount"))
    product_click = _int(info.get("productClickCount"))
    feed_exp = _int(info.get("feedExposureCount"))

    direct_amount = _int(extra.get("directPayAmountInCents")) / 100.0
    direct_num = _int(extra.get("directPayNum"))
    direct_roi = _int(extra.get("directPayRoi")) / 100.0
    indirect_amount = _int(extra.get("indirectPayAmountInCents")) / 100.0
    total_roi = _int(extra.get("totalPayRoi")) / 100.0

    click_rate = (product_click / product_exp * 100) if product_exp else 0
    cpm = (cost / exposure * 1000) if exposure else 0

    return {
        "总消耗": round(cost, 2),
        "直接成交金额": round(direct_amount, 2),
        "直接成交订单数": direct_num,
        "直接成交ROI": round(direct_roi, 2),
        "直播间曝光人数": exposure,
        "短视频曝光次数": feed_exp,
        "直播间观看人数": join_count,
        "直播间商品点击率": round(click_rate, 2),
        "直播间平均千次展示费用": round(cpm, 2),
        "总成交ROI": round(total_roi, 2),
        "间接成交金额": round(indirect_amount, 2),
        "直播间观看人次": join_pv,
    }


def _parse_order_list(data: dict) -> list[dict]:
    """从 searchLivePromotionOrderList 解析数据明细（每行）"""
    raw = data.get("data") or {}
    orders = raw.get("orderList") or []
    result = []

    for o in orders:
        pid = o.get("promotionId")
        if not pid:
            continue
        info = (o.get("indicatorData") or {}).get("dataInfo") or {}
        order_info = o.get("orderInfo") or {}
        extra = (o.get("promotionExtraIndicator") or {}).get("productIndicator") or {}
        live_cost = (o.get("promotionExtraIndicator") or {}).get("liveCost") or info.get("cost") or 0

        cost = _int(info.get("cost") or live_cost) / 10.0
        exposure = _int(info.get("exposureCount"))
        join_count = _int(info.get("joinCount"))
        join_pv = _int(info.get("joinCountPv"))
        product_exp = _int(info.get("productExposureCount"))
        product_click = _int(info.get("productClickCount"))

        direct_amount = _int(extra.get("directPayAmountInCents")) / 100.0
        direct_num = _int(extra.get("directPayNum"))
        total_roi = _int(extra.get("totalPayRoi")) / 100.0
        click_rate = (product_click / product_exp * 100) if product_exp else 0
        cpm = (cost / exposure * 1000) if exposure else 0
        enter_rate = (join_count / exposure * 100) if exposure else 0
        gpm = (direct_amount / exposure * 1000) if exposure else 0
        convert_rate = (direct_num / join_count * 100) if join_count else 0
        convert_cost = (cost / direct_num) if direct_num else 0

        def _ts_str(ts):
            if not ts:
                return ""
            try:
                return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                return str(ts)

        acct = order_info.get("acctInfo") or {}
        duration = _int(order_info.get("actualDuration"))
        duration_str = f"{duration // 60}分钟" if duration else ""

        row = {
            "promotion_id": pid,
            "加热主播与创建时间": acct.get("nickName") or "",
            "名称与编号": order_info.get("orderName") or "",
            "加热开始时间": _ts_str(order_info.get("startTime")),
            "加热结束时间": _ts_str(order_info.get("endTime")),
            "实际加热时长": duration_str,
            "总消耗": round(cost, 2),
            "直播间消耗": round(cost, 2),
            "直播间曝光人数": exposure,
            "直播间进入率_人数": round(enter_rate, 2),
            "直播间观看人数": join_count,
            "直播间商品点击率": round(click_rate, 2),
            "总成交金额": round(direct_amount, 2),
            "GPM": round(gpm, 2),
            "总成交ROI": round(total_roi, 2),
            "直播间转化率": round(convert_rate, 2),
            "直播间转化成本": round(convert_cost, 2),
            "直播间平均千次展示费用": round(cpm, 2),
            "直播间观看人次": join_pv,
            "直接成交金额": round(direct_amount, 2),
            "直接成交订单数": direct_num,
            "直接成交ROI": round(_int(extra.get("directPayRoi")) / 100.0, 2),
            "短视频曝光次数": _int(info.get("feedExposureCount")),
            "间接成交金额": round(_int(extra.get("indirectPayAmountInCents")) / 100.0, 2),
        }
        result.append(row)
    return result


def _merge_dedup(overview: dict, detail_rows: list[dict]) -> dict | None:
    """
    合并自选指标与数据明细，重复字段只保留一个（优先数据明细）
    返回与 promotion_id 关联的合并结果；多订单时取第一个匹配
    """
    if not detail_rows:
        return overview if overview else None

    merged = {}
    detail = detail_rows[0]

    for k, v in detail.items():
        merged[k] = v

    for k, v in (overview or {}).items():
        if k in DEDUP_KEYS and k in merged:
            if merged[k] == v:
                continue
        if k not in merged:
            merged[k] = v

    return merged


def collect_ecommerce_statistic(page: Page, promotion_id: str) -> dict | None:
    """
    从订单详情页点击「查看详情」进入电商加热效果页，采集自选指标+数据明细，去重后保存
    :param page: 当前应在订单详情页（live-promote-order-detail-new?id=xxx）
    :return: 合并后的统计 dict，失败返回 None
    """
    overview_responses = []
    order_list_responses = []

    def on_response(response: Response):
        if "channels.weixin.qq.com" not in response.url:
            return
        if response.status not in (200, 201) or not response.body():
            return
        data = _parse_json(response.body())
        if not data or data.get("errCode") != 0:
            return
        if API_OVERVIEW in response.url:
            overview_responses.append(data)
        elif API_ORDER_LIST in response.url:
            order_list_responses.append(data)

    page.on("response", on_response)

    log(TAG_ECOMMERCE, f"点击「查看详情」| pid={promotion_id}")
    try:
        page.get_by_text("查看详情").first.click(timeout=8000)
    except Exception as e:
        log_error(TAG_ECOMMERCE, f"点击失败 | pid={promotion_id}", e)
        return None

    page.wait_for_url(lambda u: "live-promote-statistic" in u, timeout=10000)
    page.wait_for_timeout(3500)

    overview = _parse_overview(overview_responses[-1]) if overview_responses else {}
    detail_rows = []
    for r in order_list_responses:
        rows = _parse_order_list(r)
        for row in rows:
            if row.get("promotion_id") == promotion_id:
                detail_rows.append(row)
                break
        if detail_rows:
            break
    if not detail_rows and order_list_responses:
        detail_rows = _parse_order_list(order_list_responses[-1])

    merged = _merge_dedup(overview, detail_rows)
    if merged:
        merged["promotion_id"] = promotion_id
        save_order_ecommerce_statistic(promotion_id, merged)
        log(TAG_ECOMMERCE, f"保存完成 | pid={promotion_id} | 指标数={len(merged)}")
    else:
        log(TAG_ECOMMERCE, f"无有效数据 | pid={promotion_id} | overview={bool(overview)} | detail_rows={len(detail_rows)}")
    return merged
