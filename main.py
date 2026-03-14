"""主入口 - 启动数据展示页，默认账号管理，轮询执行添加账号/采集"""
import threading
import time
import webbrowser
from pathlib import Path

from playwright.sync_api import sync_playwright

from config import COST_MIN, HOME_URL, UI_CONFIG_FILE, WEB_HOST, WEB_PORT
from port_util import ensure_port_free
from create_order_collector import collect_create_order_config
from db import count_orders, get_account
from logger import TAG_CREATE_ORDER, TAG_DETAIL, TAG_ECOMMERCE, TAG_MAIN, TAG_PEOPLE, log, log_order
from order_detail_collector import collect_detail_data
from order_statistic_collector import collect_ecommerce_statistic
from people_statistic_collector import collect_people_statistic
from account_collector import add_account_flow
from login import get_login_url
from order_collector import collect_order_data_page_by_page
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
    """执行采集流程：翻一页采一页，每页未采集的订单立即执行详情链"""

    def on_page_orders(orders: list[dict], page_num: int) -> None:
        total = len(orders)
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

    log(TAG_MAIN, f"订单列表 | 采集（账号={account_id}，时间={start_time or '全量'}~{end_time or '全量'}）")
    collect_order_data_page_by_page(
        page,
        on_page_orders=on_page_orders,
        save_to_db=True,
        account_id=account_id,
        start_time=start_time or None,
        end_time=end_time or None,
    )
    n = count_orders(cost_min=COST_MIN, account_id=account_id)
    log(TAG_MAIN, f"订单列表 | 完成 | 该账号共 {n} 条")
    log(TAG_MAIN, "采集完成")


def main():
    log(TAG_MAIN, "启动")

    if not ensure_port_free(WEB_PORT):
        log(TAG_MAIN, f"端口 {WEB_PORT} 无法释放，退出")
        return

    from app import run_app
    server = threading.Thread(target=run_app, kwargs={"host": WEB_HOST, "port": WEB_PORT}, daemon=True)
    server.start()
    log(TAG_MAIN, f"数据展示页已启动 http://{WEB_HOST}:{WEB_PORT}")
    time.sleep(1.5)
    webbrowser.open(f"http://127.0.0.1:{WEB_PORT}")
    log(TAG_MAIN, "已自动打开数据展示页与账号管理页")

    cfg = _load_ui_config()
    width, height = _parse_resolution(cfg.get("resolution", "1280x800"))
    headless = cfg.get("mode", "prod") != "dev"
    log(TAG_MAIN, f"浏览器模式: {'无头(生产)' if headless else '有头(开发)'} | 分辨率: {width}x{height}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)

        while True:
            action = read_and_clear()
            if action:
                act = action.get("action") or ""
                if act == "add_account":
                    log(TAG_MAIN, "收到添加账号请求，使用独立页面打开登录")
                    add_browser = p.chromium.launch(headless=False) if headless else browser
                    add_ctx = add_browser.new_context(
                        viewport={"width": width, "height": height},
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    )
                    add_page = add_ctx.new_page()
                    add_page.on("dialog", lambda d: d.accept())
                    add_page.goto(get_login_url(), wait_until="domcontentloaded")
                    add_account_flow(add_page, add_ctx)
                    add_ctx.close()
                    if headless:
                        add_browser.close()
                    log(TAG_MAIN, "添加账号完成，UI 页保持不动")
                elif act == "open_browser":
                    ob_account_id = (action.get("account_id") or "").strip()
                    if ob_account_id:
                        ob_acc = get_account(ob_account_id)
                        if ob_acc and ob_acc.get("cookie_path") and Path(ob_acc["cookie_path"]).exists():
                            log(TAG_MAIN, f"打开浏览器 | 账号={ob_acc.get('account_name', ob_account_id)}")
                            ob_browser = p.chromium.launch(headless=False) if headless else browser
                            ob_ctx = ob_browser.new_context(
                                viewport={"width": width, "height": height},
                                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                                storage_state=str(ob_acc["cookie_path"]),
                            )
                            ob_page = ob_ctx.new_page()
                            ob_page.on("dialog", lambda d: d.accept())
                            ob_page.goto(HOME_URL, wait_until="domcontentloaded")
                            log(TAG_MAIN, f"已打开账号首页 | {ob_acc.get('account_name', '')}")
                            if headless:
                                log(TAG_MAIN, "有头浏览器已打开，关闭窗口即结束")
                        else:
                            log(TAG_MAIN, f"账号 {ob_account_id} 不存在或 cookie 无效")
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
                                try:
                                    collect_page = collect_ctx.new_page()
                                    collect_page.on("dialog", lambda d: d.accept())
                                    _run_collect_flow(collect_page, account_id, start_time, end_time)
                                except Exception as e:
                                    log(TAG_MAIN, f"采集异常: {type(e).__name__}: {e}")
                                finally:
                                    collect_ctx.close()
                                log(TAG_MAIN, "采集任务结束，浏览器保持打开")
            time.sleep(2)


if __name__ == "__main__":
    main()
