"""订单采集模块 - 跳转订单列表页，自动翻页，增量采集，断点续传"""
import json
import math
from datetime import datetime

from playwright.sync_api import Page, Response

from config import COST_MIN, FULL_RESYNC, HOME_URL, MAX_PAGES, ORDER_LIST_URL
from checkpoint import clear_checkpoint, save_checkpoint
from db import get_existing_promotion_ids, save_orders
from logger import TAG_ORDER_LIST, log


def _parse_ts(s: str) -> datetime | None:
    """解析 createTime 为 datetime，支持多种格式"""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:19] if len(s) >= 19 else s, fmt)
        except ValueError:
            continue
    if s.isdigit():
        ts = int(s)
        if ts > 1e12:
            ts = ts / 1000
        return datetime.fromtimestamp(ts)
    return None


def _in_time_range(create_time: str, start_ts: datetime | None, end_ts: datetime | None) -> bool:
    """判断订单创建时间是否在范围内"""
    ct = _parse_ts(create_time)
    if not ct:
        return True
    if start_ts and ct < start_ts:
        return False
    if end_ts and ct > end_ts:
        return False
    return True

ORDER_API_PATTERNS = [
    "searchLivePromotionOrderList",
    "searchRoi2PromotionOrderList",
]


def _get_api_name(url: str) -> str:
    for p in ORDER_API_PATTERNS:
        if p in url:
            return p
    return "unknown"


def _parse_json_body(body: bytes) -> dict | None:
    text = body.decode("utf-8", errors="replace")
    return json.loads(text)


def _safe_get_response_body(response: Response) -> dict | None:
    """安全获取响应 body，部分响应无 body 会抛错"""
    try:
        body = response.body()
        if not body:
            return None
        return _parse_json_body(body)
    except Exception:
        return None


def _get_order_cost(order: dict) -> float:
    indicator = order.get("indicatorData") or {}
    data_info = indicator.get("dataInfo") or {}
    cost_str = data_info.get("cost") or order.get("cost")
    return float(cost_str) if cost_str else 0.0


def _filter_by_cost(orders: list[dict], min_cost_yuan: float) -> list[dict]:
    min_cost_dou = min_cost_yuan * 10
    return [o for o in orders if _get_order_cost(o) > min_cost_dou]


def _extract_order_list_from_response(data: dict) -> list[dict]:
    raw = data.get("data") or {}
    inner = raw.get("data", raw)
    return inner.get("orderList") or inner.get("list") or []


def collect_order_data(
    page: Page,
    cost_min: float | None = None,
    save_to_db: bool = True,
    account_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[dict]:
    """
    跳转到订单列表页，自动翻页，增量采集（遇已存在则停止），断点续传
    - start_time/end_time: 可选，格式 "YYYY-MM-DD" 或 "YYYY-MM-DD HH:MM:SS"
    - 全不选=全量；仅开始=开始之后；仅结束=结束之前；都选=区间内
    - 已采集的订单跳过
    """
    cost_min = cost_min if cost_min is not None else COST_MIN
    start_ts = _parse_ts(start_time) if start_time else None
    end_ts = _parse_ts(end_time) if end_time else None
    if FULL_RESYNC:
        clear_checkpoint()
    existing_ids = set() if FULL_RESYNC else (get_existing_promotion_ids() if save_to_db else set())
    collected: list[dict] = []
    list_responses: list[dict] = []

    def on_response(response: Response):
        url = response.url or ""
        if "searchLivePromotionOrderList" not in url and "searchRoi2PromotionOrderList" not in url:
            return
        if response.status not in (200, 201):
            return
        data = _safe_get_response_body(response)
        if not data or data.get("error"):
            return
        list_responses.append({"url": url, "data": data})
        collected.append({"api": _get_api_name(url), "url": url, "data": data})

    page.on("response", on_response)
    log(TAG_ORDER_LIST, "先进入首页以建立会话")
    page.goto(HOME_URL, wait_until="networkidle")
    page.wait_for_timeout(2000)
    if "login" in page.url:
        log(TAG_ORDER_LIST, "会话可能已过期，当前在登录页，请重新添加账号")
        return collected

    log(TAG_ORDER_LIST, "跳转订单列表页")
    with page.expect_response(
        lambda r: "searchLivePromotionOrderList" in (r.url or ""),
        timeout=15000,
    ):
        page.goto(ORDER_LIST_URL, wait_until="networkidle")
    page.wait_for_timeout(2000)
    if "login" in page.url:
        log(TAG_ORDER_LIST, "会话可能已过期，请重新添加账号")
        return collected

    tab = page.locator("text=标准订单").first
    if tab.is_visible():
        with page.expect_response(
            lambda r: "searchLivePromotionOrderList" in (r.url or ""),
            timeout=10000,
        ):
            tab.click()
        page.wait_for_timeout(2000)

    total = 0
    live_items = [i for i in list_responses if "searchLivePromotionOrderList" in i.get("url", "")]
    if not live_items:
        log(TAG_ORDER_LIST, "未捕获到列表 API，尝试刷新...")
        with page.expect_response(
            lambda r: "searchLivePromotionOrderList" in (r.url or ""),
            timeout=10000,
        ):
            page.reload(wait_until="networkidle")
        page.wait_for_timeout(2000)
        live_items = [i for i in list_responses if "searchLivePromotionOrderList" in i.get("url", "")]

    for item in live_items:
        raw = item.get("data") or {}
        inner = raw.get("data", raw)
        t = inner.get("total") or inner.get("totalCount")
        if t is not None:
            total = int(t)
            break
        ol = inner.get("orderList") or inner.get("list") or []
        if ol:
            total = len(ol)
            break

    page_size = 20
    total_pages = math.ceil(total / page_size) if total else 1
    if MAX_PAGES is not None:
        total_pages = min(total_pages, MAX_PAGES)
    log(TAG_ORDER_LIST, f"共 {total} 条 | 已有 {len(existing_ids)} 条在库 | 需翻至多 {total_pages} 页")

    processed_count = 0
    stop_pagination = False

    def process_responses(start_idx: int, page_num: int) -> bool:
        """处理从 start_idx 起的新响应（仅主列表 searchLive），返回是否应停止翻页"""
        nonlocal stop_pagination, processed_count
        live_responses = [
            (i, item) for i, item in enumerate(list_responses[start_idx:], start_idx)
            if "searchLivePromotionOrderList" in item.get("url", "")
        ]
        for i, item in live_responses:
            orders = _extract_order_list_from_response(item.get("data") or {})
            to_save = []
            for o in orders:
                pid = o.get("promotionId")
                if not pid:
                    continue
                if not FULL_RESYNC and pid in existing_ids:
                    stop_pagination = True
                    log(TAG_ORDER_LIST, f"第 {page_num} 页 | 遇已采集 {pid[:20]}... | 停止翻页（增量边界）")
                    break
                create_time = o.get("createTime") or ""
                if not _in_time_range(create_time, start_ts, end_ts):
                    continue
                existing_ids.add(pid)
                to_save.append(o)
            if stop_pagination:
                break
            filtered = _filter_by_cost(to_save, cost_min)
            if filtered and save_to_db:
                n = save_orders(filtered, account_id=account_id)
                processed_count += n
                save_checkpoint(page_num, total_pages, processed_count)
                log(TAG_ORDER_LIST, f"第 {page_num} 页 | 新增 {n} 条 | 累计 {processed_count}")
        return stop_pagination

    process_responses(0, 1)
    if stop_pagination:
        log(TAG_ORDER_LIST, f"增量完成 | 共新增 {processed_count} 条（消耗>{cost_min}）")
        return collected

    for page_num in range(2, total_pages + 1):
        next_btn = page.locator("a:has-text('下一页')").first
        if not next_btn.is_visible():
            break
        idx_before = len(list_responses)
        log(TAG_ORDER_LIST, f"翻页 {page_num}/{total_pages}")
        with page.expect_response(
            lambda r: "searchLivePromotionOrderList" in r.url or "searchRoi2PromotionOrderList" in r.url,
            timeout=15000,
        ):
            next_btn.click()
        page.wait_for_timeout(500)
        process_responses(idx_before, page_num)
        if stop_pagination:
            break

    if processed_count > 0:
        log(TAG_ORDER_LIST, f"采集完成 | 共新增 {processed_count} 条（消耗>{cost_min}）")
    return collected


def extract_order_list(collected: list[dict]) -> list[dict]:
    seen: set[str] = set()
    orders = []
    for item in collected:
        data = item.get("data") or {}
        orders_raw = _extract_order_list_from_response(data)
        for o in orders_raw:
            pid = o.get("promotionId")
            if pid and pid not in seen:
                seen.add(pid)
                orders.append(o)
    return orders


def extract_filtered_orders(collected: list[dict], cost_min: float | None = None) -> list[dict]:
    orders = extract_order_list(collected)
    min_yuan = cost_min if cost_min is not None else COST_MIN
    return _filter_by_cost(orders, min_yuan)
