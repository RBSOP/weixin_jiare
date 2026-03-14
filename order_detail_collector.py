"""订单详情页数据采集 - 拦截 getLivePromotionOrderDetail、getLivePromotionOrdersTsIndicator 获取数据"""
import json
import re
from datetime import datetime, timezone, timedelta

from playwright.sync_api import Page, Response

TZ_CN = timezone(timedelta(hours=8))

from db import get_order_create_config, save_order_detail_data, update_order_bid_roi_if_empty
from logger import TAG_DETAIL, log
from screenshot_util import capture_page_screenshot

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
    m = {0: "不限", 1: "男性", 2: "女性"}
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
    city_str = "不限城市" if not city_ids else f"已选{len(city_ids)}个城市"

    device_types = suggest.get("deviceTypes") or suggest.get("device") or suggest.get("deviceType")
    device_str = "全部"
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
            return datetime.fromtimestamp(int(ts), tz=TZ_CN).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return str(ts)

    def _format_duration(sec) -> str:
        if sec is None or sec == "":
            return ""
        s = int(sec)
        if s < 60:
            return f"{s}秒"
        if s < 3600:
            return f"{s // 60}分钟"
        h, m = s // 3600, (s % 3600) // 60
        return f"{h}小时{m}分钟" if m else f"{h}小时"

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
        "实际加热时长": _format_duration(order_info.get("actualDuration")),
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


def _extract_effect_summary_from_dom(page: Page) -> dict:
    """
    从订单详情页 .data-item-container 区域 DOM 直接提取直播间加热效果汇总
    结构：每个 .mb-12 子块含 label（text-black/50）+ value（span）
    """
    try:
        loc = page.locator(".data-item-container")
        if loc.count() > 0:
            loc.first.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
        result = page.evaluate(
            """
            () => {
                const container = document.querySelector('.data-item-container');
                if (!container) return {};
                const out = {};
                const labelOrder = ['消耗总金额','曝光总人数','进入总人数','点赞总次数','评论总次数','新增总关注'];
                const items = Array.from(container.children).filter(el => el.classList.contains('mb-12'));
                items.forEach(item => {
                    const labelDiv = item.querySelector('.mb-4.text-center, [class*="text-black"]');
                    if (!labelDiv) return;
                    const label = (labelDiv.textContent || '').trim();
                    if (!labelOrder.includes(label)) return;
                    const valContainer = item.querySelector('.flex.items-center.justify-center');
                    if (!valContainer) return;
                    let val = '';
                    const wsSpan = valContainer.querySelector('span.font-ws');
                    if (wsSpan) {
                        val = (wsSpan.textContent || '').trim();
                    } else {
                        const spans = valContainer.querySelectorAll('span');
                        const numSpan = Array.from(spans).find(s => /^[\\d.]+$/.test((s.textContent||'').trim()));
                        if (numSpan) val = (numSpan.textContent || '').trim();
                    }
                    if (val !== '') out[label] = isNaN(Number(val)) ? val : Number(val);
                });
                return out;
            }
            """
        )
        if isinstance(result, dict) and result:
            log(TAG_DETAIL, f"DOM提取直播间加热效果: {len(result)} 项")
        return result if isinstance(result, dict) else {}
    except Exception as e:
        log(TAG_DETAIL, f"DOM提取直播间加热效果失败: {e}")
        return {}


_ECOMMERCE_EFFECT_LABELS = [
    "总成交ROI", "成交GMV", "商品点击人数", "商品点击次数",
    "下单订单数", "下单GMV", "成交订单数",
]


def _extract_ecommerce_effect_from_dom(page: Page) -> dict:
    """
    从订单详情页「电商加热效果」卡片 DOM 提取 7 项指标。
    卡片标题为「电商加热效果」，与「直播间加热效果」(.data-item-container) 独立。
    """
    try:
        result = page.evaluate(
            """
            () => {
                const labels = %s;
                const cards = document.querySelectorAll('.finder-card');
                let target = null;
                for (const card of cards) {
                    const title = card.querySelector('.text-xl');
                    if (title && title.textContent.trim() === '电商加热效果') {
                        target = card;
                        break;
                    }
                }
                if (!target) return {};
                const out = {};
                const items = target.querySelectorAll('.data-item-container > div');
                items.forEach(item => {
                    const labelEl = item.querySelector('.text-black\\\\/50, .text-sm');
                    if (!labelEl) return;
                    const label = labelEl.textContent.trim();
                    if (!labels.includes(label)) return;
                    const valContainer = item.querySelector('.flex.items-center');
                    if (!valContainer) return;
                    const span = valContainer.querySelector('span');
                    if (!span) return;
                    let raw = (span.textContent || '').trim();
                    raw = raw.replace(/^[￥¥]/, '');
                    out[label] = isNaN(Number(raw)) ? raw : Number(raw);
                });
                return out;
            }
            """ % str(_ECOMMERCE_EFFECT_LABELS).replace("'", '"')
        )
        if isinstance(result, dict) and result:
            log(TAG_DETAIL, f"DOM提取电商加热效果: {len(result)} 项 {list(result.keys())}")
        return result if isinstance(result, dict) else {}
    except Exception as e:
        log(TAG_DETAIL, f"DOM提取电商加热效果失败: {e}")
        return {}


def _extract_heating_info_from_dom(page: Page) -> dict:
    """
    从订单详情页 #promote-info 区域 DOM 直接提取加热信息，与页面展示一致
    """
    try:
        result = page.evaluate(
            """
            () => {
                const card = document.getElementById('promote-info');
                if (!card) return {};
                const out = {};
                const content = card.querySelector('.content');
                if (content) {
                    const txt = content.textContent || '';
                    const m1 = txt.match(/名称[：:]\\s*([^\\s编号]+)/);
                    if (m1) out['订单名称'] = m1[1].trim();
                    const m2 = txt.match(/编号[：:]\\s*(\\S+)/);
                    if (m2) out['编号'] = m2[1].trim();
                }
                const basisDivs = card.querySelectorAll('[class*="basis-1"]');
                if (basisDivs.length >= 1) {
                    const d0 = basisDivs[0];
                    const allText = (d0.textContent || '').trim();
                    const label = '加热目标';
                    const val = allText.replace(label, '').replace(/^[：:\\s]+/, '').trim();
                    if (val && val !== label) out['加热目标'] = val;
                }
                if (basisDivs.length >= 2) {
                    const statusEl = basisDivs[1].querySelector('.status');
                    if (statusEl) out['状态'] = statusEl.textContent?.trim() || '';
                }
                const cells = card.querySelectorAll('.table-cell');
                const labels = ['加热预算','预计时长','下单时间','开始时间','结束时间','实际加热时长','加热出价','放量模式','观众性别','根据粉丝层推荐','根据名单推荐','观众城市','观众年龄','观众设备','观众兴趣'];
                cells.forEach(cell => {
                    const labelEl = cell.querySelector('.font-medium') || cell.querySelector('[class*="w-28"]');
                    if (!labelEl) return;
                    const label = labelEl.textContent?.trim() || '';
                    if (!labels.includes(label)) return;
                    let val = '';
                    const span = cell.querySelector('span.font-ws');
                    if (span) {
                        val = (span.textContent || '').trim();
                    } else {
                        const spans = cell.querySelectorAll('span');
                        for (let i = spans.length - 1; i >= 0; i--) {
                            const t = (spans[i].textContent || '').trim();
                            if (t && t !== label) { val = t; break; }
                        }
                    }
                    if (!val) {
                        const full = (cell.textContent || '').replace(/\\s+/g, ' ').trim();
                        val = full.replace(label, '').replace(/^[：:\\s]+/, '').trim();
                    }
                    if (val) out[label] = val;
                });
                return out;
            }
            """
        )
        if isinstance(result, dict) and result:
            log(TAG_DETAIL, f"DOM提取加热信息: {len(result)} 项")
        return result if isinstance(result, dict) else {}
    except Exception as e:
        log(TAG_DETAIL, f"DOM提取加热信息失败: {e}")
        return {}


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
        if response.status not in (200, 201):
            return
        try:
            body = response.body()
            if not body:
                return
            data = _parse_json_body(body)
        except Exception:
            return
        if not data:
            return
        err = data.get("errCode")
        if err is not None and err != 0:
            log(TAG_DETAIL, f"API返回错误: errCode={err} | url={response.url[-60:]}")
            return
        if API_DETAIL in response.url:
            detail_responses.append(data)
        elif API_TS_INDICATOR in response.url:
            ts_responses.append(data)

    page.on("response", on_response)
    log(TAG_DETAIL, f"跳转详情页 | pid={promotion_id}")
    goto_detail_page(page, promotion_id)
    page.wait_for_timeout(8000)
    page.remove_listener("response", on_response)

    heating_info = {}
    if detail_responses:
        heating_info = _parse_detail_response(detail_responses[-1], promotion_id, order_row)

    def _normalize_date_str(s: str) -> str:
        if not s or not isinstance(s, str):
            return s
        m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}:\d{2}:\d{2})", s.strip())
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d} {m.group(4)}"
        return s

    dom_info = _extract_heating_info_from_dom(page)
    if dom_info:
        crowd_keys = {"观众性别", "根据粉丝层推荐", "根据名单推荐", "观众年龄", "观众设备", "观众城市", "观众兴趣"}
        time_keys = {"下单时间", "开始时间", "结束时间"}
        if "人群定向" not in heating_info:
            heating_info["人群定向"] = {}
        for k, v in dom_info.items():
            if not v:
                continue
            if k in time_keys and isinstance(v, str):
                v = _normalize_date_str(v)
            if k == "实际加热时长" and str(v).isdigit():
                v = f"{int(v) // 60}分钟" if int(v) < 3600 else f"{int(v) // 3600}小时{(int(v) % 3600) // 60}分钟"
            elif k == "观众性别" and v in ("男", "女"):
                v = "男性" if v == "男" else "女性"
            elif k == "观众设备" and v == "不限":
                v = "全部"
            elif k == "观众城市" and v == "全部地区":
                v = "不限城市"
            if k in crowd_keys:
                heating_info["人群定向"][k] = v
            elif k not in crowd_keys:
                heating_info[k] = v

    cfg = get_order_create_config(promotion_id)
    if cfg:
        crowd = cfg.get("人群定向") or {}
        if isinstance(crowd, dict):
            interest = crowd.get("观众兴趣", "")
            if interest and isinstance(heating_info.get("人群定向"), dict):
                heating_info["人群定向"]["观众兴趣"] = interest

    effect_summary = {}
    effect_timeline = []
    if ts_responses:
        effect_summary, effect_timeline = _parse_ts_indicator_response(ts_responses[-1])

    dom_effect = _extract_effect_summary_from_dom(page)
    if dom_effect and len(dom_effect) >= 4:
        for k, v in dom_effect.items():
            if v is not None and v != "":
                effect_summary[k] = float(v) if isinstance(v, (int, float)) else v
        log(TAG_DETAIL, f"直播间加热效果已用DOM覆盖: {list(dom_effect.keys())}")

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

    ecommerce_effect = _extract_ecommerce_effect_from_dom(page)

    save_order_detail_data(
        promotion_id,
        heating_info,
        consumption_progress,
        effect_summary,
        effect_timeline,
        ecommerce_effect=ecommerce_effect or None,
    )
    bid_val = heating_info.get("加热出价")
    if bid_val is not None:
        update_order_bid_roi_if_empty(promotion_id, str(bid_val))
    has_heating = bool(heating_info)
    has_effect = bool(effect_summary or effect_timeline)
    has_ecommerce = bool(ecommerce_effect)
    log(TAG_DETAIL, f"保存完成 | pid={promotion_id} | 加热信息={has_heating} | 效果数据={has_effect} | 电商加热效果={has_ecommerce}")
    capture_page_screenshot(page, promotion_id, "order_detail", TAG_DETAIL)
    return {
        "加热信息": heating_info,
        "消耗进度": consumption_progress,
        "直播间加热效果": effect_summary,
        "十分钟级数据": effect_timeline,
        "电商加热效果": ecommerce_effect,
    }
