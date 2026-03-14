"""订单采集模块 - 按时间范围加载全部订单，筛选状态与入库状态后保存"""
import json
import math
from datetime import datetime, timezone, timedelta

from playwright.sync_api import Page, Response

from config import COST_MIN, FULL_RESYNC, HOME_URL, MAX_PAGES, ORDER_LIST_URL
from db import get_existing_promotion_ids, is_order_detail_collected, save_orders
from logger import TAG_ORDER_LIST, log

# 微信加热平台使用东八区（中国时间）
TZ_CN = timezone(timedelta(hours=8))


def _parse_ts(s: str) -> datetime | None:
    """
    解析 createTime 为 datetime（东八区），支持多种格式
    API 返回的 Unix 时间戳按 UTC 解析后转东八区，与用户输入的本地时间一致
    """
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
            ts = ts // 1000
        return datetime.fromtimestamp(ts, tz=TZ_CN).replace(tzinfo=None)
    return None


def _in_time_range(create_time: str, start_ts: datetime | None, end_ts: datetime | None) -> bool:
    """判断订单创建时间是否在范围内。无法解析时排除（避免误采范围外订单）"""
    ct = _parse_ts(create_time)
    if not ct:
        if start_ts or end_ts:
            return False
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
            log(TAG_ORDER_LIST, f"响应 body 为空: {response.url[-60:]}")
            return None
        return _parse_json_body(body)
    except Exception as e:
        log(TAG_ORDER_LIST, f"读取响应 body 失败: {type(e).__name__}: {e} | url={response.url[-80:]}")
        return None


def _get_order_cost(order: dict) -> float:
    indicator = order.get("indicatorData") or {}
    data_info = indicator.get("dataInfo") or {}
    cost_str = data_info.get("cost") or order.get("cost")
    return float(cost_str) if cost_str else 0.0


def _filter_by_cost(orders: list[dict], min_cost_yuan: float) -> list[dict]:
    min_cost_dou = min_cost_yuan * 10
    return [o for o in orders if _get_order_cost(o) > min_cost_dou]


def _filter_completed_only(orders: list[dict]) -> list[dict]:
    """只保留订单状态为已完成的订单（status=3）"""
    return [o for o in orders if str(o.get("status") or "") == "3"]


def _api_order_to_row(o: dict) -> dict:
    """将 API 订单转为 order_row 格式（供详情链使用）"""
    order_info = o.get("orderInfo") or {}
    acct = o.get("acctInfo") or order_info.get("acctInfo") or {}
    target = o.get("targetName") or o.get("target") or _map_promotion_target(
        o.get("promotionTarget") or order_info.get("promotionTarget")
    )
    return {
        "promotion_id": o.get("promotionId"),
        "order_name": o.get("promotionName") or o.get("orderName") or order_info.get("orderName") or order_info.get("promotionName") or "",
        "target": target,
        "create_time": o.get("createTime") or order_info.get("createTime") or "",
        "nick_name": acct.get("nickName") or "",
    }


def _map_promotion_target(v) -> str:
    m = {8: "直播间成交", 10: "观看量", 11: "成交ROI", 12: "互动量"}
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _extract_order_list_from_response(data: dict) -> list[dict]:
    raw = data.get("data") or {}
    inner = raw.get("data", raw)
    return inner.get("orderList") or inner.get("list") or []


def _apply_order_list_date_filter(
    page: Page, start_ts: datetime, end_ts: datetime, list_responses: list
) -> None:
    """
    在订单列表页设置开始/结束日期并触发查询，使 API 仅返回时间范围内的订单。
    清空已有响应，等待新的筛选结果。
    """
    start_str = start_ts.strftime("%Y-%m-%d")
    end_str = end_ts.strftime("%Y-%m-%d")
    log(TAG_ORDER_LIST, f"设置日期筛选: {start_str} ~ {end_str}")
    try:
        inputs = page.locator("input[type='date']").all()
        if len(inputs) >= 2:
            list_responses.clear()
            page.wait_for_timeout(300)
            inputs[0].fill(start_str)
            page.wait_for_timeout(200)
            inputs[1].fill(end_str)
            page.wait_for_timeout(200)
        else:
            start_inp = page.locator("input[placeholder*='开始'], input[placeholder*='日期']").first
            end_inp = page.locator("input[placeholder*='结束'], input[placeholder*='至']").first
            if start_inp.count() > 0 and end_inp.count() > 0:
                list_responses.clear()
                start_inp.fill(start_str)
                page.wait_for_timeout(200)
                end_inp.fill(end_str)
                page.wait_for_timeout(200)
            else:
                log(TAG_ORDER_LIST, "未找到日期输入框，将依赖后端时间过滤")
                return
        btn = page.locator("button:has-text('查询'), button:has-text('搜索'), .van-button:has-text('查询')").first
        if btn.is_visible():
            with page.expect_response(
                lambda r: "searchLivePromotionOrderList" in (r.url or ""),
                timeout=10000,
            ):
                btn.click()
        else:
            page.keyboard.press("Enter")
        page.wait_for_timeout(2500)
    except Exception as e:
        log(TAG_ORDER_LIST, f"设置日期筛选异常: {e} | 将依赖后端时间过滤")


def collect_order_data(
    page: Page,
    cost_min: float | None = None,
    save_to_db: bool = True,
    account_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[dict]:
    """
    跳转到订单列表页，按时间范围加载全部订单，筛选后入库
    - start_time/end_time: 可选，格式 "YYYY-MM-DD" 或 "YYYY-MM-DD HH:MM:SS"
    - 全不选=全量；仅开始=开始之后；仅结束=结束之前；都选=区间内
    - 翻页加载完所有页后，按状态(已完成)、时间范围、消耗、是否已入库筛选，未入库的保存
    """
    cost_min = cost_min if cost_min is not None else COST_MIN
    start_ts = _parse_ts(start_time) if start_time else None
    end_ts = _parse_ts(end_time) if end_time else None
    log(TAG_ORDER_LIST, "只采集订单状态为「已完成」的订单")
    db_ids = set() if FULL_RESYNC else (get_existing_promotion_ids() if save_to_db else set())
    seen_this_run: set[str] = set()
    collected: list[dict] = []
    list_responses: list[dict] = []

    def on_response(response: Response):
        url = response.url or ""
        if "searchLivePromotionOrderList" not in url and "searchRoi2PromotionOrderList" not in url:
            return
        api_name = _get_api_name(url)
        log(TAG_ORDER_LIST, f"捕获API响应: {api_name} | status={response.status}")
        if response.status not in (200, 201):
            log(TAG_ORDER_LIST, f"跳过非正常状态: {response.status}")
            return
        data = _safe_get_response_body(response)
        if not data:
            log(TAG_ORDER_LIST, f"跳过: 响应 body 解析失败")
            return
        if data.get("error"):
            log(TAG_ORDER_LIST, f"跳过: API返回错误 {data.get('error')}")
            return
        inner = (data.get("data") or {})
        inner = inner.get("data", inner) if isinstance(inner, dict) else inner
        ol_count = len(inner.get("orderList") or inner.get("list") or []) if isinstance(inner, dict) else 0
        t = inner.get("total") if isinstance(inner, dict) else None
        log(TAG_ORDER_LIST, f"响应解析成功: {api_name} | total={t} | orderList={ol_count}条")
        list_responses.append({"url": url, "data": data})
        collected.append({"api": api_name, "url": url, "data": data})

    page.on("response", on_response)
    try:
        log(TAG_ORDER_LIST, "先进入首页以建立会话")
        page.goto(HOME_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if "login" in page.url:
            log(TAG_ORDER_LIST, "会话可能已过期，当前在登录页，请重新添加账号")
            return collected

        log(TAG_ORDER_LIST, "跳转订单列表页")
        with page.expect_response(
            lambda r: "searchLivePromotionOrderList" in (r.url or ""),
            timeout=15000,
        ):
            page.goto(ORDER_LIST_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if "login" in page.url:
            log(TAG_ORDER_LIST, "会话可能已过期，请重新添加账号")
            return collected

        try:
            tab = page.locator("text=标准订单").first
            if tab.is_visible():
                with page.expect_response(
                    lambda r: "searchLivePromotionOrderList" in (r.url or ""),
                    timeout=10000,
                ):
                    tab.click()
                page.wait_for_timeout(2000)
        except Exception:
            log(TAG_ORDER_LIST, "标准订单 tab 点击或等待响应超时，使用已有数据继续")
            page.wait_for_timeout(1000)

        if start_ts and end_ts:
            _apply_order_list_date_filter(page, start_ts, end_ts, list_responses)

        total = 0
        live_items = [i for i in list_responses if "searchLivePromotionOrderList" in i.get("url", "")]
        if not live_items:
            log(TAG_ORDER_LIST, "未捕获到列表 API，尝试刷新...")
            with page.expect_response(
                lambda r: "searchLivePromotionOrderList" in (r.url or ""),
                timeout=10000,
            ):
                page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            live_items = [i for i in list_responses if "searchLivePromotionOrderList" in i.get("url", "")]

        log(TAG_ORDER_LIST, f"共捕获 {len(live_items)} 个 searchLivePromotionOrderList 响应")
        for idx, item in enumerate(live_items):
            raw = item.get("data") or {}
            inner = raw.get("data", raw)
            t_total = inner.get("total")
            t_count = inner.get("totalCount")
            t = t_total if t_total is not None else t_count
            ol = inner.get("orderList") or inner.get("list") or []
            log(TAG_ORDER_LIST, f"响应[{idx}]: total={t}, orderList={len(ol)}条")
            if t is not None and int(t) > 0:
                total = max(total, int(t))
            if ol:
                total = max(total, len(ol))

        page_size = 20
        total_pages = math.ceil(total / page_size) if total else 1
        if MAX_PAGES is not None:
            total_pages = min(total_pages, MAX_PAGES)
        log(TAG_ORDER_LIST, f"共 {total} 条 | 需翻 {total_pages} 页 | 已有 {len(db_ids)} 条在库")

        for page_num in range(2, total_pages + 1):
            next_btn = page.locator("a:has-text('下一页')").first
            if not next_btn.is_visible():
                log(TAG_ORDER_LIST, f"第 {page_num} 页无下一页按钮，翻页结束")
                break
            log(TAG_ORDER_LIST, f"翻页 {page_num}/{total_pages}")
            with page.expect_response(
                lambda r: "searchLivePromotionOrderList" in r.url or "searchRoi2PromotionOrderList" in r.url,
                timeout=15000,
            ):
                next_btn.click()
            page.wait_for_timeout(2000)

        all_orders: list[dict] = []
        live_responses = [i for i in list_responses if "searchLivePromotionOrderList" in i.get("url", "")]
        for item in live_responses:
            orders = _extract_order_list_from_response(item.get("data") or {})
            for o in orders:
                pid = o.get("promotionId")
                if not pid or pid in seen_this_run:
                    continue
                seen_this_run.add(pid)
                all_orders.append(o)

        orders_completed = _filter_completed_only(all_orders)
        orders_in_range = [o for o in orders_completed if _in_time_range(
            o.get("createTime") or (o.get("orderInfo") or {}).get("createTime") or "",
            start_ts, end_ts,
        )]
        orders_by_cost = _filter_by_cost(orders_in_range, cost_min)
        to_save = orders_by_cost if FULL_RESYNC else [o for o in orders_by_cost if o.get("promotionId") not in db_ids]

        if to_save and save_to_db:
            n = save_orders(to_save, account_id=account_id)
            log(TAG_ORDER_LIST, f"筛选完成 | 时间范围内 {len(orders_in_range)} 条 | 消耗>{cost_min} 共 {len(orders_by_cost)} 条 | 未入库 {len(to_save)} 条 | 新增 {n} 条")
        else:
            skipped = len(orders_by_cost) - len(to_save) if not FULL_RESYNC else 0
            log(TAG_ORDER_LIST, f"筛选完成 | 时间范围内 {len(orders_in_range)} 条 | 消耗>{cost_min} 共 {len(orders_by_cost)} 条 | 已入库跳过 {skipped} 条 | 待新增 0 条")
        return collected
    finally:
        page.remove_listener("response", on_response)


def collect_order_data_page_by_page(
    page: Page,
    on_page_orders,
    cost_min: float | None = None,
    save_to_db: bool = True,
    account_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> None:
    """
    翻一页采一页：每翻一页立即检测该页未采集的订单并回调，采完当前页再翻下一页。
    on_page_orders(orders: list[dict]) 接收当前页待采集的订单（order_row 格式），执行详情链。
    """
    cost_min = cost_min if cost_min is not None else COST_MIN
    start_ts = _parse_ts(start_time) if start_time else None
    end_ts = _parse_ts(end_time) if end_time else None
    log(TAG_ORDER_LIST, "只采集订单状态为「已完成」的订单")
    db_ids = set() if FULL_RESYNC else (get_existing_promotion_ids() if save_to_db else set())
    list_responses: list[dict] = []
    PAGE_RESPONSE_TIMEOUT = 30000

    def on_response(response: Response):
        url = response.url or ""
        if "searchLivePromotionOrderList" not in url and "searchRoi2PromotionOrderList" not in url:
            return
        if response.status not in (200, 201):
            return
        data = _safe_get_response_body(response)
        if not data or data.get("error"):
            return
        log(TAG_ORDER_LIST, f"捕获API响应: searchLivePromotionOrderList | status={response.status}")
        list_responses.append({"url": url, "data": data})

    page.on("response", on_response)
    try:
        log(TAG_ORDER_LIST, "先进入首页以建立会话")
        page.goto(HOME_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if "login" in page.url:
            log(TAG_ORDER_LIST, "会话可能已过期，当前在登录页，请重新添加账号")
            return

        log(TAG_ORDER_LIST, "跳转订单列表页")
        with page.expect_response(
            lambda r: "searchLivePromotionOrderList" in (r.url or ""),
            timeout=15000,
        ):
            page.goto(ORDER_LIST_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if "login" in page.url:
            log(TAG_ORDER_LIST, "会话可能已过期，请重新添加账号")
            return

        try:
            tab = page.locator("text=标准订单").first
            if tab.is_visible():
                with page.expect_response(
                    lambda r: "searchLivePromotionOrderList" in (r.url or ""),
                    timeout=10000,
                ):
                    tab.click()
                page.wait_for_timeout(2000)
        except Exception:
            page.wait_for_timeout(1000)

        if start_ts and end_ts:
            _apply_order_list_date_filter(page, start_ts, end_ts, list_responses)

        live_items = [i for i in list_responses if "searchLivePromotionOrderList" in i.get("url", "")]
        if not live_items:
            with page.expect_response(
                lambda r: "searchLivePromotionOrderList" in (r.url or ""),
                timeout=10000,
            ):
                page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            live_items = [i for i in list_responses if "searchLivePromotionOrderList" in i.get("url", "")]

        total = 0
        for item in live_items:
            inner = (item.get("data") or {}).get("data") or item.get("data") or {}
            if isinstance(inner, dict):
                t = inner.get("total") or inner.get("totalCount")
                ol = inner.get("orderList") or inner.get("list") or []
                if t is not None and int(t) > 0:
                    total = max(total, int(t))
                if ol:
                    total = max(total, len(ol))

        page_size = 20
        total_pages = math.ceil(total / page_size) if total else 1
        if MAX_PAGES is not None:
            total_pages = min(total_pages, MAX_PAGES)
        log(TAG_ORDER_LIST, f"共 {total} 条 | 需翻 {total_pages} 页 | 翻一页采一页")

        def _get_page_orders_from_responses() -> list[dict]:
            items = [i for i in list_responses if "searchLivePromotionOrderList" in i.get("url", "")]
            if not items:
                return []
            return _extract_order_list_from_response(items[-1].get("data") or {})

        def _process_page(page_num: int, raw_orders: list[dict]) -> None:
            seen = set()
            unique = []
            for o in raw_orders:
                pid = o.get("promotionId")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                unique.append(o)
            completed = _filter_completed_only(unique)
            in_range = [o for o in completed if _in_time_range(
                o.get("createTime") or (o.get("orderInfo") or {}).get("createTime") or "",
                start_ts, end_ts,
            )]
            by_cost = _filter_by_cost(in_range, cost_min)
            to_save = by_cost if FULL_RESYNC else [o for o in by_cost if o.get("promotionId") not in db_ids]
            if to_save and save_to_db:
                save_orders(to_save, account_id=account_id)
                for o in to_save:
                    db_ids.add(o.get("promotionId"))
            to_collect = [o for o in by_cost if o.get("promotionId") and not is_order_detail_collected(o.get("promotionId"))]
            if to_collect:
                rows = [_api_order_to_row(o) for o in to_collect]
                log(TAG_ORDER_LIST, f"第 {page_num} 页 | 本页 {len(by_cost)} 条 | 待采 {len(to_collect)} 条")
                on_page_orders(rows, page_num)

        page_1_orders = _get_page_orders_from_responses()
        _process_page(1, page_1_orders)

        def _go_back_to_order_list() -> bool:
            """返回订单列表页并切换到标准订单，成功返回 True"""
            with page.expect_response(
                lambda r: "searchLivePromotionOrderList" in (r.url or ""),
                timeout=15000,
            ):
                page.goto(ORDER_LIST_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            try:
                tab = page.locator("text=标准订单").first
                if tab.is_visible():
                    with page.expect_response(
                        lambda r: "searchLivePromotionOrderList" in (r.url or ""),
                        timeout=10000,
                    ):
                        tab.click()
                    page.wait_for_timeout(1500)
            except Exception:
                pass
            return "order-list" in page.url or "live" in page.url

        def _goto_page(target_page: int) -> bool:
            """从第1页点击下一页 (target_page-1) 次到达目标页"""
            next_btn = page.locator("a:has-text('下一页'), a:has-text('>')").first
            for _ in range(target_page - 1):
                if not next_btn.is_visible():
                    return False
                list_responses.clear()
                try:
                    with page.expect_response(
                        lambda r: "searchLivePromotionOrderList" in (r.url or ""),
                        timeout=PAGE_RESPONSE_TIMEOUT,
                    ):
                        next_btn.click()
                except Exception:
                    return False
                page.wait_for_timeout(1500)
            return True

        for page_num in range(2, total_pages + 1):
            log(TAG_ORDER_LIST, f"返回订单列表以翻页 {page_num}/{total_pages}")
            _go_back_to_order_list()
            list_responses.clear()
            if not _goto_page(page_num):
                log(TAG_ORDER_LIST, f"第 {page_num} 页无法到达，翻页结束")
                break
            page_orders = _get_page_orders_from_responses()
            _process_page(page_num, page_orders)

        log(TAG_ORDER_LIST, "翻一页采一页完成")
    finally:
        page.remove_listener("response", on_response)


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
