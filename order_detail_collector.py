"""订单详情页数据采集 - 拦截 getLivePromotionOrderDetail、getLivePromotionOrdersTsIndicator 获取数据"""
import json
from datetime import datetime

from playwright.sync_api import Page, Response

from db import save_order_detail_data
from logger import TAG_DETAIL, log

# 详情页实际调用的接口（MCP 分析得出）
API_DETAIL = "getLivePromotionOrderDetail"
API_TS_INDICATOR = "getLivePromotionOrdersTsIndicator"


def _parse_json_body(body: bytes) -> dict | None:
    text = body.decode("utf-8", errors="replace")
    return json.loads(text)


def _match_api(url: str) -> bool:
    if "channels.weixin.qq.com" not in url:
        return False
    return API_DETAIL in url or API_TS_INDICATOR in url


def _map_target(v) -> str:
    m = {8: "直播间成交", 10: "观看量", 11: "成交ROI", 12: "互动量"}
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_gender(v) -> str:
    m = {0: "不限", 1: "男", 2: "女"}
    if isinstance(v, list) and v:
        v = v[0]
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_age_list(codes) -> list[str]:
    m = {1: "18-23岁", 2: "24-30岁", 3: "31-40岁", 4: "41-50岁", 5: "51岁以上"}
    if not codes:
        return []
    return [m.get(int(c), str(c)) for c in (codes if isinstance(codes, list) else [codes])]


def _parse_detail_response(data: dict, promotion_id: str, order_row: dict | None) -> dict:
    """
    解析 getLivePromotionOrderDetail 响应（MCP 验证：有 orderInfo）
    结构: data.livePromotionOrderDetailInfo.{status, dataInfo, orderInfo}
    orderInfo: acctInfo, suggest, costQuota, orderName, promotionTarget, deliverySpeedMode, materialFlag, createTime, startTime, endTime, duration, actualDuration
    """
    raw = data.get("data") or {}
    info = raw.get("livePromotionOrderDetailInfo") or {}
    if not info:
        return {}

    order_info = info.get("orderInfo") or {}
    acct = order_info.get("acctInfo") or {}
    suggest = order_info.get("suggest") or {}

    status_map = {"0": "待开始", "1": "进行中", "2": "已完成", "3": "已完成"}
    status = status_map.get(str(info.get("status", "")), str(info.get("status", "")))

    cost_quota = info.get("costQuota") or order_info.get("costQuota") or 0
    cost_yuan = int(cost_quota) / 10.0 if cost_quota else 0

    gender = suggest.get("gender")
    age_range = suggest.get("ageRange") or suggest.get("ageRangeList") or []
    city_ids = suggest.get("cityIds") or []
    interest_tag = suggest.get("interestTagV3") or suggest.get("interestTag") or []
    interest_labels = []
    for t in (interest_tag if isinstance(interest_tag, list) else []):
        if isinstance(t, dict):
            interest_labels.append(t.get("name") or t.get("label") or str(t))
        else:
            interest_labels.append(str(t))
    interest_str = "、".join(interest_labels[:5]) + ("..." if len(interest_labels) > 5 else "") if interest_labels else "未添加"
    city_str = "全部地区" if not city_ids else f"已选{len(city_ids)}个城市"

    device_types = suggest.get("deviceTypes") or suggest.get("device") or suggest.get("deviceType")
    device_str = "不限"
    if device_types:
        if isinstance(device_types, list):
            if 1 in device_types and 2 not in device_types:
                device_str = "iOS"
            elif 2 in device_types and 1 not in device_types:
                device_str = "安卓"
        elif device_types == 1:
            device_str = "iOS"
        elif device_types == 2:
            device_str = "安卓"

    fan_target = suggest.get("fanTarget") or suggest.get("similarAuthorFan") or suggest.get("similarAcctList")
    list_target = suggest.get("listTarget") or suggest.get("specifiedList") or suggest.get("liveUinPackageIds")
    fan_recommend = "选择相似作者的粉丝" if fan_target else "不限"
    list_recommend = "选择指定名单" if list_target else "不限"

    def _ts_to_str(ts):
        if not ts:
            return ""
        try:
            return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return str(ts)

    return {
        "编号": promotion_id,
        "订单名称": order_info.get("orderName") or order_info.get("promotionName") or (order_row.get("order_name") if order_row else ""),
        "加热目标": _map_target(order_info.get("promotionTarget")) or (order_row.get("target") if order_row else ""),
        "状态": status,
        "加热预算": round(cost_yuan, 2),
        "预计时长": f"{int(order_info.get('duration') or 0) // 3600}小时" if order_info.get("duration") else "",
        "下单时间": _ts_to_str(order_info.get("createTime")) or (order_row.get("create_time") if order_row else ""),
        "开始时间": _ts_to_str(order_info.get("startTime")),
        "结束时间": _ts_to_str(order_info.get("endTime")),
        "实际加热时长": order_info.get("actualDuration") or "",
        "目标成交ROI": str(order_info.get("bidRoi") or order_info.get("targetRoi") or suggest.get("roiBidX100", "")),
        "放量模式": "匀速放量" if order_info.get("deliverySpeedMode") == 1 else ("快速放量" if order_info.get("deliverySpeedMode") == 2 else ""),
        "主播昵称": acct.get("nickName") or "",
        "人群定向": {
            "观众性别": _map_gender(gender),
            "根据粉丝层推荐": fan_recommend,
            "根据名单推荐": list_recommend,
            "观众年龄": _map_age_list(age_range),
            "观众设备": device_str,
            "观众城市": city_str,
            "观众兴趣": interest_str,
        },
    }


def _parse_ts_indicator_response(data: dict) -> tuple[dict, list]:
    """
    解析 getLivePromotionOrdersTsIndicator 响应
    实际结构: data.tsDataInfoList[{sampleTime, dataInfo{cost, exposureCount, joinCount, likeCount, commentCount, followCount}}]
    返回: (effect_summary, effect_timeline)
    """
    raw = data.get("data") or {}
    ts_list = raw.get("tsDataInfoList") or []
    if not isinstance(ts_list, list):
        return {}, []

    def _int(v):
        if v is None:
            return 0
        if isinstance(v, str):
            return int(v) if v.isdigit() else 0
        return int(v)

    timeline = []
    last_data = {}
    for item in ts_list:
        if not isinstance(item, dict):
            continue
        di = item.get("dataInfo") or {}
        sample_time = item.get("sampleTime")
        time_str = datetime.fromtimestamp(sample_time).strftime("%H:%M") if sample_time else ""

        row = {
            "时间": time_str,
            "sampleTime": sample_time,
            "直播间消耗": _int(di.get("cost")) / 10.0,
            "直播间曝光人数": _int(di.get("exposureCount")),
            "直播间观看人数": _int(di.get("joinCount")),
            "直播间点赞次数": _int(di.get("likeCount")),
            "直播间评论次数": _int(di.get("commentCount")),
            "直播间新增粉丝数": _int(di.get("followCount")),
        }
        timeline.append(row)
        last_data = di

    effect_summary = {}
    if last_data:
        cost_dou = _int(last_data.get("cost"))
        effect_summary = {
            "消耗总金额": round(cost_dou / 10.0, 2),
            "曝光总人数": _int(last_data.get("exposureCount")),
            "进入总人数": _int(last_data.get("joinCount")),
            "点赞总次数": _int(last_data.get("likeCount")),
            "评论总次数": _int(last_data.get("commentCount")),
            "新增总关注": _int(last_data.get("followCount")),
        }

    return effect_summary, timeline


def collect_detail_data(
    page: Page,
    promotion_id: str,
    order_row: dict | None = None,
) -> dict | None:
    """
    跳转到详情页，拦截 getLivePromotionOrderDetail、getLivePromotionOrdersTsIndicator，解析并保存
    :param order_row: 可选，来自 orders 表的订单行，用于补充加热信息（订单名称、加热目标、预算、时间）
    :return: 解析后的完整数据 dict
    """
    from order_detail import goto_detail_page

    detail_responses: list[dict] = []
    ts_responses: list[dict] = []

    def on_response(response: Response):
        if "channels.weixin.qq.com" not in response.url:
            return
        if response.status not in (200, 201) or not response.body():
            return
        data = _parse_json_body(response.body())
        if not data or data.get("errCode") != 0:
            return
        if API_DETAIL in response.url:
            detail_responses.append(data)
        elif API_TS_INDICATOR in response.url:
            ts_responses.append(data)

    page.on("response", on_response)
    log(TAG_DETAIL, f"跳转详情页 | pid={promotion_id}")
    goto_detail_page(page, promotion_id)
    page.wait_for_timeout(4000)

    heating_info = {}
    if detail_responses:
        heating_info = _parse_detail_response(detail_responses[-1], promotion_id, order_row)

    effect_summary = {}
    effect_timeline = []
    if ts_responses:
        effect_summary, effect_timeline = _parse_ts_indicator_response(ts_responses[-1])

    cost_total = effect_summary.get("消耗总金额", 0)
    budget = heating_info.get("加热预算")
    if budget is None and order_row and order_row.get("budget") is not None:
        budget = round(order_row["budget"] / 10.0, 2)
    cost_dou = int(cost_total * 10)
    budget_dou = int(budget * 10) if budget else 0
    consumption_progress = {
        "消耗微信豆": cost_dou,
        "预算微信豆": budget_dou,
        "消耗金额_元": cost_total,
        "预算金额_元": round(budget, 2) if budget else 0,
        "消耗进度": f"{cost_dou}/{budget_dou}" if budget_dou else str(cost_dou),
    }

    save_order_detail_data(
        promotion_id,
        heating_info,
        consumption_progress,
        effect_summary,
        effect_timeline,
    )
    has_heating = bool(heating_info)
    has_effect = bool(effect_summary or effect_timeline)
    log(TAG_DETAIL, f"保存完成 | pid={promotion_id} | 加热信息={has_heating} | 效果数据={has_effect}")
    return {
        "加热信息": heating_info,
        "消耗进度": consumption_progress,
        "直播间加热效果": effect_summary,
        "十分钟级数据": effect_timeline,
    }
