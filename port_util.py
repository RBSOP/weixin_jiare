"""端口检测与清理 - 兼容 Windows / Mac / Linux"""
import platform
import socket
import subprocess
import sys
import time

from logger import TAG_MAIN, log


def is_port_in_use(port: int) -> bool:
    """检测端口是否被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _get_pids_windows(port: int) -> list[int]:
    """Windows: 通过 netstat 获取占用端口的 PID"""
    out = subprocess.run(
        ["netstat", "-ano"],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if out.returncode != 0:
        return []
    pids = []
    needle = f":{port}"
    for line in out.stdout.splitlines():
        if needle in line:
            parts = line.split()
            if parts:
                try:
                    pid = int(parts[-1])
                    if pid > 0 and pid not in pids:
                        pids.append(pid)
                except ValueError:
                    pass
    return pids


def _get_pids_unix(port: int) -> list[int]:
    """Mac/Linux: 通过 lsof 获取占用端口的 PID"""
    out = subprocess.run(
        ["lsof", "-i", f":{port}", "-t"],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0 or not out.stdout.strip():
        return []
    return [int(x) for x in out.stdout.strip().split() if x.isdigit()]


def _kill_pids(pids: list[int]) -> bool:
    """根据平台终止进程"""
    if not pids:
        return False
    is_win = platform.system() == "Windows"
    for pid in pids:
        if is_win:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        else:
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)
    return True


def ensure_port_free(port: int) -> bool:
    """
    若端口被占用则清理后返回 True；未被占用直接返回 True；清理失败返回 False
    """
    if not is_port_in_use(port):
        return True
    log(TAG_MAIN, f"端口 {port} 被占用，尝试清理")
    is_win = platform.system() == "Windows"
    pids = _get_pids_windows(port) if is_win else _get_pids_unix(port)
    if not pids:
        log(TAG_MAIN, f"无法获取占用端口 {port} 的进程，请手动关闭")
        return False
    log(TAG_MAIN, f"终止占用端口的进程: PIDs={pids}")
    _kill_pids(pids)
    time.sleep(1)
    if is_port_in_use(port):
        log(TAG_MAIN, f"清理后端口 {port} 仍被占用")
        return False
    log(TAG_MAIN, f"端口 {port} 已释放")
    return True
