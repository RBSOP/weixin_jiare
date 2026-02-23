"""账号采集 - 从首页/账户信息页提取账号信息并保存 cookie"""
import json
import re
import uuid
from pathlib import Path

from playwright.sync_api import Page, Response

from config import ACCOUNT_COOKIE_DIR, HOME_URL
from db import save_account
from logger import TAG_MAIN, log

ACCOUNT_INFO_URL = "https://channels.weixin.qq.com/promote/pages/platform/common-account-info"


def _extract_from_get_user_prepare(data: dict) -> tuple[str, str]:
    """
    优先从 getUserPrepare 接口提取（首页加载时调用）
    data.corporateUserInfo.corporateInfo.corporateName -> 账户名
    data.corporateUserInfo.corporateInfo.finderNickname -> 视频号
    """
    account_name = ""
    video_account = ""
    try:
        corp = (data.get("data") or {}).get("corporateUserInfo", {}).get("corporateInfo", {})
        if corp:
            account_name = corp.get("corporateName") or corp.get("corpName") or ""
            video_account = corp.get("finderNickname") or corp.get("nickname") or ""
    except Exception:
        pass
    return account_name or "", video_account or ""


def _extract_from_json(obj: dict) -> tuple[str, str]:
    """从 JSON 对象中递归查找账户名、视频号"""
    account_name = ""
    video_account = ""
    keys_name = ("corporateName", "nickName", "nick_name", "accountName", "corpName", "name", "finderName", "finder_name")
    keys_video = ("finderNickname", "videoAccount", "video_account", "finderId", "finder_id", "finderUsername")

    skip_vals = ("RpcError", "error", "undefined", "null", "")

    def walk(o, depth=0):
        nonlocal account_name, video_account
        if depth > 5:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, str) and 0 < len(v) < 100 and v not in skip_vals:
                    if k in keys_name and not account_name:
                        account_name = v
                    elif k in keys_video and not video_account:
                        video_account = v
                elif isinstance(v, (dict, list)):
                    walk(v, depth + 1)
        elif isinstance(o, list):
            for item in o[:5]:
                walk(item, depth + 1)

    walk(obj)
    return account_name, video_account


def _safe_get_response_body(response: Response) -> dict | None:
    """安全获取响应 body，部分响应无 body 会抛错"""
    try:
        body = response.body()
        if not body:
            return None
        return json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return None


def extract_account_from_home(page: Page) -> tuple[str, str]:
    """
    从首页、账户信息页提取账户名、视频号
    1. 先拦截首页 API（getUserPrepare 等）
    2. 再跳转账户信息页拦截 API
    3. 最后从页面 DOM/HTML 兜底
    """
    account_name = ""
    video_account = ""
    api_data: list[dict] = []

    def on_response(response: Response):
        url = response.url or ""
        if response.status not in (200, 201):
            return
        if "channels.weixin.qq.com" not in url or "/api/" not in url:
            return
        ctype = (response.headers.get("content-type") or "").lower()
        if "json" not in ctype:
            return
        data = _safe_get_response_body(response)
        if data and not data.get("error"):
            api_data.append({"url": url, "data": data})

    page.on("response", on_response)
    page.goto(HOME_URL, wait_until="networkidle")
    page.wait_for_timeout(2500)
    page.goto(ACCOUNT_INFO_URL, wait_until="networkidle")
    page.wait_for_timeout(2500)

    for item in api_data:
        raw = item.get("data") or {}
        if raw.get("data", {}).get("corporateUserInfo"):
            an, va = _extract_from_get_user_prepare(raw)
            if an:
                account_name = an
            if va:
                video_account = va
    for item in api_data:
        raw = item.get("data") or {}
        if raw.get("data", {}).get("corporateUserInfo"):
            continue
        an, va = _extract_from_json(raw)
        if an and not account_name:
            account_name = an
        if va and not video_account:
            video_account = va

    if not account_name or not video_account:
        try:
            text = page.content()
            if not account_name:
                m = re.search(r'["'](?:nickName|nick_name|accountName|name|corpName)["']\s*:\s*["']([^"']+)["']', text)
                if m:
                    account_name = m.group(1)
            if not video_account:
                m = re.search(r'["'](?:videoAccount|video_account|finderId|finder_id)["']\s*:\s*["']([^"']+)["']', text)
                if m:
                    video_account = m.group(1)
        except Exception:
            pass

    if not account_name:
        account_name = "未知账户"
    return account_name, video_account


def add_account_flow(page: Page, context) -> dict | None:
    """
    添加账号流程：当前页应在登录页，等待扫码 -> 首页 -> 提取账号 -> 保存 cookie
    返回 {"id", "account_name", "video_account", "cookie_path"} 或 None
    """
    from login import wait_for_login

    log(TAG_MAIN, "等待扫码登录...")
    wait_for_login(page, timeout=600000)
    log(TAG_MAIN, "登录成功，跳转首页...")
    from home import goto_home
    goto_home(page)
    page.wait_for_timeout(2000)
    log(TAG_MAIN, "提取账号信息...")
    account_name, video_account = extract_account_from_home(page)
    account_id = str(uuid.uuid4())[:8]
    Path(ACCOUNT_COOKIE_DIR).mkdir(parents=True, exist_ok=True)
    cookie_path = str(Path(ACCOUNT_COOKIE_DIR) / f"{account_id}.json")
    context.storage_state(path=cookie_path)
    save_account(account_id, account_name, video_account, cookie_path)
    log(TAG_MAIN, f"账号已保存: {account_name} / {video_account}")
    return {"id": account_id, "account_name": account_name, "video_account": video_account, "cookie_path": cookie_path}
