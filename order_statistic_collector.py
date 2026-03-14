"""电商加热效果统计采集 - 自选指标 + 数据明细，去重后与订单ID关联"""
import json
import re
from datetime import datetime

from playwright.sync_api import Page, Response

from db import save_order_ecommerce_statistic
from logger import TAG_ECOMMERCE, log, log_error
from screenshot_util import capture_page_screenshot

API_OVERVIEW = "getLivePromotionOrderOverview"
API_ORDER_LIST = "searchLivePromotionOrderList"

# 电商加热效果 12 项自选指标顺序（与页面 DOM 一致）
ECOMMERCE_ORDER = [
    "总消耗", "直接成交金额", "直接成交订单数", "直接成交ROI",
    "直播间曝光人数", "短视频曝光次数", "直播间观看人数", "直播间商品点击率",
    "直播间平均千次展示费用", "总成交ROI", "间接成交金额", "直播间观看人次",
]

# 数据明细表需采集的 9 列（与页面一致）
DETAIL_TABLE_COLS = [
    "加热主播与创建时间", "名称与编号", "加热开始时间", "加热结束时间", "实际加热时长",
    "直播间进入率（人数）", "GPM", "直播间转化率", "直播间转化成本",
]

# 自选指标与数据明细重复字段（数值相同时只保留一个）
DEDUP_KEYS = {
    "总消耗", "直播间消耗", "直播间曝光人数", "直播间观看人数",
    "直播间商品点击率", "总成交ROI", "直播间平均千次展示费用",
    "总成交金额", "直接成交金额",
}
# 数据明细表 DOM 优先（API 时间戳可能有时区问题，转化成本公式与页面不一致，时长格式不同）
PREFER_DOM_KEYS = {"加热主播与创建时间", "加热开始时间", "加热结束时间", "实际加热时长", "直播间转化成本"}


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

    click_rate = (product_click / join_pv * 100) if join_pv else 0
    cpm = (cost / exposure * 100) if exposure else 0

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
        click_rate = (product_click / join_pv * 100) if join_pv else 0
        cpm = (cost / exposure * 100) if exposure else 0
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

        def _create_time_str(ts):
            if not ts:
                return ""
            try:
                t = int(ts)
                if t > 1e12:
                    t = t // 1000
                return datetime.fromtimestamp(t).strftime("%Y年%m月%d日 %H:%M")
            except (ValueError, TypeError):
                return str(ts)

        acct = order_info.get("acctInfo") or {}
        nick = acct.get("nickName") or ""
        create_time = _create_time_str(o.get("createTime") or order_info.get("createTime"))
        anchor_create = f"{nick} {create_time}".strip() if create_time else nick

        duration = _int(order_info.get("actualDuration"))
        duration_str = f"{duration // 60}分钟" if duration else ""

        row = {
            "promotion_id": pid,
            "加热主播与创建时间": anchor_create,
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


def _parse_dom_value(text: str, label: str):
    """解析 DOM 文本为数值"""
    text = (text or "").strip().replace(" ", "").replace("\u00a0", "")
    if not text:
        return 0
    text = text.replace("￥", "").replace("¥", "")
    if "%" in text:
        try:
            return round(float(re.sub(r"[^\d.]", "", text)), 2)
        except (ValueError, TypeError):
            return 0
    try:
        num = float(re.sub(r"[^\d.]", "", text))
        return int(num) if num == int(num) else round(num, 2)
    except (ValueError, TypeError):
        return 0


def _extract_ecommerce_from_dom(page: Page) -> dict | None:
    """从电商加热效果页 DOM 按顺序提取 12 项指标"""
    try:
        items = page.locator('div.grid[class*="grid-cols-6"] > div[class*="flex-col"]').all()
        label_to_idx = {label: i for i, label in enumerate(ECOMMERCE_ORDER)}
        result = {}
        for item in items:
            label_el = item.locator("span.text-center").first
            if not label_el.count():
                continue
            label = (label_el.text_content() or "").strip()
            if label not in label_to_idx:
                continue
            value_el = item.locator("div.mt-5").first
            if not value_el.count():
                continue
            value_text = value_el.inner_text() or ""
            result[label] = _parse_dom_value(value_text, label)
        if len(result) < 12:
            return None
        ordered = {k: result.get(k, 0) for k in ECOMMERCE_ORDER}
        return ordered
    except Exception:
        return None


def _parse_detail_cell_text(cell) -> str:
    """从表格单元格提取文本（去除多余空白）"""
    try:
        text = (cell.inner_text() or "").strip()
        return " ".join(text.split())
    except Exception:
        return ""


def _normalize_detail_date(s: str) -> str:
    """将 2026年03月02日 15:25 转为 2026-03-02 15:25:00"""
    if not s or not isinstance(s, str):
        return s
    s = s.strip()
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}:\d{2})(?::\d{2})?", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d} {m.group(4)}:00"[:19]
    return s


def _extract_detail_table_from_dom(page: Page, promotion_id: str) -> dict | None:
    """
    从数据明细表 DOM 提取指定 9 列（优先主表，避免左侧固定列表格）
    主表 17 列顺序：加热主播与创建时间, 名称与编号, 加热开始时间, 加热结束时间, 实际加热时长,
    总消耗, 直播间消耗, 直播间曝光人数, 直播间进入率（人数）, 直播间观看人数, 直播间商品点击率,
    总成交金额, GPM, 总成交ROI, 直播间转化率, 直播间转化成本, 直播间平均千次展示费用
    """
    try:
        for selector in (
            'div.finder-ui-desktop-table__main table.finder-ui-desktop-table__core-table',
            'div.orderlist-table table.finder-ui-desktop-table__core-table',
            'div[class*="orderlist"] table',
            'table.finder-ui-desktop-table__core-table',
        ):
            table = page.locator(selector).first
            if not table.count():
                continue
            rows = table.locator("tbody tr")
            if rows.count() < 1:
                continue

            for row_idx in range(rows.count()):
                cells = rows.nth(row_idx).locator("td").all()
                if len(cells) < 16:
                    continue

                row_data = {}
                cell0 = cells[0]
                nickname_el = cell0.locator(".nickname")
                createtime_el = cell0.locator(".createtime")
                if nickname_el.count() and createtime_el.count():
                    nick = (nickname_el.first.inner_text() or "").strip()
                    ctime = (createtime_el.first.inner_text() or "").strip()
                    row_data["加热主播与创建时间"] = f"{nick} {ctime}".strip() if ctime else nick
                elif nickname_el.count():
                    row_data["加热主播与创建时间"] = (nickname_el.first.inner_text() or "").strip()
                else:
                    row_data["加热主播与创建时间"] = _parse_detail_cell_text(cell0)

                cell1 = cells[1]
                ordername_el = cell1.locator(".ordername")
                orderid_el = cell1.locator(".orderid")
                if ordername_el.count() or orderid_el.count():
                    name = ordername_el.first.inner_text().strip() if ordername_el.count() else ""
                    oid = orderid_el.first.inner_text().strip() if orderid_el.count() else ""
                    row_data["名称与编号"] = f"{name} {oid}".strip()
                else:
                    row_data["名称与编号"] = _parse_detail_cell_text(cell1)

                row_data["加热开始时间"] = _normalize_detail_date(_parse_detail_cell_text(cells[2]))
                row_data["加热结束时间"] = _normalize_detail_date(_parse_detail_cell_text(cells[3]))
                row_data["实际加热时长"] = _parse_detail_cell_text(cells[4])
                row_data["直播间进入率（人数）"] = _parse_detail_cell_text(cells[8])
                row_data["GPM"] = _parse_detail_cell_text(cells[12])
                row_data["直播间转化率"] = _parse_detail_cell_text(cells[14])
                row_data["直播间转化成本"] = _parse_detail_cell_text(cells[15])

                full_row_text = " ".join(_parse_detail_cell_text(c) for c in cells[:5])
                if promotion_id:
                    if rows.count() == 1:
                        pass
                    elif promotion_id not in row_data.get("名称与编号", "") and promotion_id not in full_row_text:
                        continue

                result = {}
                for k in DETAIL_TABLE_COLS:
                    v = row_data.get(k, "")
                    if k in ("直播间进入率（人数）", "直播间转化率"):
                        result[k] = _parse_dom_value(str(v), k) if "%" in str(v) else v
                    elif k in ("GPM", "直播间转化成本"):
                        result[k] = _parse_dom_value(str(v), k) if ("￥" in str(v) or "¥" in str(v)) else v
                    else:
                        result[k] = v
                return result
        return None
    except Exception:
        return None


def _merge_dedup(overview: dict, detail_rows: list[dict]) -> dict | None:
    """
    合并自选指标与数据明细，重复字段只保留一个（优先数据明细）
    PREFER_DOM_KEYS 优先使用 overview（DOM），避免 API 时间戳/公式与页面不一致
    """
    if not detail_rows:
        return overview if overview else None

    merged = {}
    detail = detail_rows[0]

    for k, v in detail.items():
        merged[k] = v

    for k, v in (overview or {}).items():
        if k in PREFER_DOM_KEYS and v:
            merged[k] = v
            continue
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
        page.remove_listener("response", on_response)
        return None

    try:
        page.wait_for_url(lambda u: "live-promote-statistic" in u, timeout=30000)
    except Exception as e:
        log_error(TAG_ECOMMERCE, f"等待跳转电商页超时 | pid={promotion_id}", e)
        page.remove_listener("response", on_response)
        return None
    page.wait_for_timeout(6000)
    page.remove_listener("response", on_response)

    try:
        page.locator('div.grid[class*="grid-cols-6"]').first.scroll_into_view_if_needed(timeout=3000)
        page.wait_for_timeout(500)
        page.locator('div.orderlist-table, div.table_out').first.scroll_into_view_if_needed(timeout=3000)
        page.wait_for_timeout(500)
    except Exception:
        pass

    overview = _parse_overview(overview_responses[-1]) if overview_responses else {}
    dom_data = _extract_ecommerce_from_dom(page)
    if dom_data:
        overview = dom_data
        log(TAG_ECOMMERCE, f"已从 DOM 采集 12 项 | pid={promotion_id}")

    detail_table = _extract_detail_table_from_dom(page, promotion_id)
    if detail_table:
        overview = overview or {}
        overview.update(detail_table)
        log(TAG_ECOMMERCE, f"已从数据明细表采集 9 列 | pid={promotion_id}")

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

    if dom_data:
        merged = dict(overview)
    else:
        merged = _merge_dedup(overview, detail_rows)
    if merged:
        ordered = {"promotion_id": promotion_id}
        for k in ECOMMERCE_ORDER:
            if k in merged:
                ordered[k] = merged[k]
        for k in DETAIL_TABLE_COLS:
            if k in merged:
                ordered[k] = merged[k]
            elif k == "直播间进入率（人数）" and "直播间进入率_人数" in merged:
                ordered[k] = merged["直播间进入率_人数"]
        save_order_ecommerce_statistic(promotion_id, ordered)
        log(TAG_ECOMMERCE, f"保存完成 | pid={promotion_id} | 指标数={len(ordered)}")
        capture_page_screenshot(page, promotion_id, "ecommerce", TAG_ECOMMERCE)
        return ordered
    log(TAG_ECOMMERCE, f"无有效数据 | pid={promotion_id} | overview={bool(overview)} | detail_rows={len(detail_rows)}")
    capture_page_screenshot(page, promotion_id, "ecommerce", TAG_ECOMMERCE)
    return None
