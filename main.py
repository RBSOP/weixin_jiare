"""主入口 - 启动数据展示页，默认账号管理，轮询执行添加账号/采集"""
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from config import COST_MIN, UI_CONFIG_FILE, WEB_HOST, WEB_PORT
from create_order_collector import collect_create_order_config
from db import count_orders, get_account, query_orders
from logger import TAG_CREATE_ORDER, TAG_DETAIL, TAG_ECOMMERCE, TAG_MAIN, TAG_PEOPLE, log, log_order
from order_detail_collector import collect_detail_data
from order_statistic_collector import collect_ecommerce_statistic
from people_statistic_collector import collect_people_statistic
from account_collector import add_account_flow
from login import get_login_url
from order_collector import collect_order_data
from pending_action import read_and_clear


def _load_ui_config() -> dict:
    """读取 UI 配置（模式、分辨率）"""
    p = Path(UI_CONFIG_FILE)
    if not p.exists():
        return {"mode": "prod", "resolution": "1280x800"}
    import json
    return json.loads(p.read_text(encoding="utf-8"))


def _parse_resolution(s: str) -> tuple[int, int]:
    """解析分辨率字符串，如 1280x800"""
    try:
        w, h = s.split("x")
        return int(w), int(h)
    except Exception:
        return 1280, 800


def _run_collect_flow(page, account_id: str, start_time: str, end_time: str) -> None:
    """执行采集流程：订单列表 -> 详情链"""
    log(TAG_MAIN, f"订单列表 | 采集（账号={account_id}，时间={start_time or '全量'}~{end_time or '全量'}）")
    collect_order_data(
        page,
        save_to_db=True,
        account_id=account_id,
        start_time=start_time or None,
        end_time=end_time or None,
    )
    n = count_orders(cost_min=COST_MIN, account_id=account_id)
    log(TAG_MAIN, f"订单列表 | 完成 | 该账号共 {n} 条")

    orders = query_orders(cost_min=COST_MIN, account_id=account_id)
    total = len(orders)
    log(TAG_MAIN, f"订单详情链 | 共 {total} 条待采集")

    for i, o in enumerate(orders):
        pid = o.get("promotion_id")
        if not pid:
            continue
        idx = i + 1

        log_order(TAG_CREATE_ORDER, idx, total, pid, "开始")
        cfg_res = collect_create_order_config(page, pid)
        log_order(TAG_CREATE_ORDER, idx, total, pid, "成功" if cfg_res else "失败")
        page.wait_for_timeout(1500)

        log_order(TAG_DETAIL, idx, total, pid, "开始")
        detail = collect_detail_data(page, pid, order_row=o)
        ok = detail and (detail.get("加热信息") or detail.get("直播间加热效果"))
        log_order(TAG_DETAIL, idx, total, pid, "成功" if ok else "失败或数据不完整")

        log_order(TAG_ECOMMERCE, idx, total, pid, "开始")
        stat = collect_ecommerce_statistic(page, pid)
        log_order(TAG_ECOMMERCE, idx, total, pid, "成功" if stat else "失败")

        log_order(TAG_PEOPLE, idx, total, pid, "开始")
        people = collect_people_statistic(page, pid)
        log_order(TAG_PEOPLE, idx, total, pid, "成功" if people else "失败")

    log(TAG_MAIN, "采集完成")


def main():
    log(TAG_MAIN, "启动")

    from app import run_app
    server = threading.Thread(target=run_app, kwargs={"host": WEB_HOST, "port": WEB_PORT}, daemon=True)
    server.start()
    log(TAG_MAIN, f"数据展示页已启动 http://{WEB_HOST}:{WEB_PORT}")

    cfg = _load_ui_config()
    width, height = _parse_resolution(cfg.get("resolution", "1280x800"))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": width, "height": height},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = context.new_page()
        page.on("dialog", lambda d: d.accept())
        page.goto(f"http://127.0.0.1:{WEB_PORT}/#accounts", wait_until="networkidle")
        log(TAG_MAIN, "已打开账号管理页")

        while True:
            action = read_and_clear()
            if action:
                act = action.get("action") or ""
                if act == "add_account":
                    log(TAG_MAIN, "收到添加账号请求，跳转登录页")
                    page.goto(get_login_url(), wait_until="networkidle")
                    add_account_flow(page, context)
                    page.goto(f"http://127.0.0.1:{WEB_PORT}/#accounts", wait_until="networkidle")
                    log(TAG_MAIN, "已返回账号管理页")
                elif act == "collect":
                    account_id = (action.get("account_id") or "").strip()
                    start_time = (action.get("start_time") or "").strip()
                    end_time = (action.get("end_time") or "").strip()
                    if not account_id:
                        log(TAG_MAIN, "采集请求缺少 account_id，跳过")
                    else:
                        acc = get_account(account_id)
                        if not acc or not acc.get("cookie_path"):
                            log(TAG_MAIN, f"账号 {account_id} 不存在或 cookie 无效，跳过")
                        else:
                            cookie_path = Path(acc["cookie_path"])
                            if not cookie_path.exists():
                                log(TAG_MAIN, f"cookie 文件不存在: {cookie_path}")
                            else:
                                collect_ctx = browser.new_context(
                                    viewport={"width": width, "height": height},
                                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                                    storage_state=str(cookie_path),
                                )
                                collect_page = collect_ctx.new_page()
                                collect_page.on("dialog", lambda d: d.accept())
                                _run_collect_flow(collect_page, account_id, start_time, end_time)
                                collect_ctx.close()
                                log(TAG_MAIN, "采集任务结束，浏览器保持打开")
            time.sleep(2)


if __name__ == "__main__":
    main()
