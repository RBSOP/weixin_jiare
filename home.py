"""首页模块 - 登录后默认在首页，等待首页加载完成"""
from playwright.sync_api import Page

from config import HOME_URL, HOME_URL_PATTERN


def wait_for_home(page: Page, timeout: int = 10000) -> bool:
    """
    等待首页加载完成
    登录成功后通常已跳转到首页，此函数用于确认
    """
    page.wait_for_url(lambda url: HOME_URL_PATTERN in url, timeout=timeout)
    return True


def goto_home(page: Page) -> None:
    """导航到首页"""
    page.goto(HOME_URL, wait_until="networkidle")


def is_on_home(page: Page) -> bool:
    """判断当前是否在首页"""
    return HOME_URL_PATTERN in page.url
