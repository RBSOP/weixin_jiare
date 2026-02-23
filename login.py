"""登录模块 - 打开加热平台登录页，等待用户扫码完成"""
from playwright.sync_api import Page

from config import LOGIN_URL, LOGIN_SUCCESS_URL_PATTERN, AUTH_STATE_FILE


def wait_for_login(page: Page, timeout: int = 600000) -> bool:
    """
    等待用户扫码登录完成
    通过检测 URL 跳转判断登录成功
    """
    page.wait_for_url(
        lambda url: LOGIN_SUCCESS_URL_PATTERN in url and "login" not in url,
        timeout=timeout,
    )
    return True


def get_login_url() -> str:
    """获取登录页 URL"""
    return LOGIN_URL


def get_auth_state_path() -> str:
    """获取登录状态保存路径"""
    return AUTH_STATE_FILE
